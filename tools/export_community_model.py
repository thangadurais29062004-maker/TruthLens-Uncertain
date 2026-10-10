"""Export the pinned authors' ViT-Small checkpoint without executing remote code."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('checkpoint',type=Path)
parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent.parent/'models/official-community-224.onnx')
args=parser.parse_args()
ROOT=Path(__file__).resolve().parent.parent/'models'
import onnx
from onnx import helper as h,numpy_helper as nh,TensorProto
RAW=args.checkpoint.read_bytes()
assert hashlib.sha256(RAW).hexdigest()=='a6cc439d5a6d2dfadd60c77d27a2838ad55b34e601ecd30f46ad97266d6ac4e0'
length=int.from_bytes(RAW[:8],'little'); header=json.loads(RAW[8:8+length]); payload=RAW[8+length:]
W={}
for name,entry in header.items():
    if name=='__metadata__':continue
    assert entry['dtype']=='F32'
    start,end=entry['data_offsets'];value=np.frombuffer(payload[start:end],dtype='<f4').reshape(entry['shape']).copy()
    assert np.isfinite(value).all();W[name]=value
nodes=[];initial=[];counter=0
constants={}
def const(value,name=None):
    global counter
    if name is None: counter+=1;name=f'constant_{counter}'
    if name not in constants: initial.append(nh.from_array(np.asarray(value),name));constants[name]=True
    return name
def node(op,*inputs,**attrs):
    global counter
    counter+=1;output=f'value_{counter}';nodes.append(h.make_node(op,list(inputs),[output],**attrs));return output
def weight(name,transpose=False):return const(W[name].T.copy() if transpose else W[name],name+('_T' if transpose else ''))
def linear(x,name):return node('Add',node('MatMul',x,weight(name+'.weight',True)),weight(name+'.bias'))
def norm(x,name):return node('LayerNormalization',x,weight(name+'.weight'),weight(name+'.bias'),axis=-1,epsilon=1e-6)
x=node('Conv','pixel_values',weight('vit.patch_embed.proj.weight'),weight('vit.patch_embed.proj.bias'),strides=[16,16])
x=node('Reshape',x,const(np.array([1,384,196],np.int64)))
x=node('Transpose',x,perm=[0,2,1]);x=node('Concat',weight('vit.cls_token'),x,axis=1)
x=node('Add',x,weight('vit.pos_embed'))
for i in range(12):
    base=f'vit.blocks.{i}';original=x
    qkv=linear(norm(x,base+'.norm1'),base+'.attn.qkv')
    qkv=node('Reshape',qkv,const(np.array([1,197,3,6,64],np.int64)))
    qkv=node('Transpose',qkv,perm=[2,0,3,1,4])
    q,k,v=[node('Gather',qkv,const(np.array(j,np.int64)),axis=0) for j in range(3)]
    scores=node('MatMul',q,node('Transpose',k,perm=[0,1,3,2]))
    attention=node('Softmax',node('Mul',scores,const(np.array(.125,np.float32))),axis=-1)
    a=node('MatMul',attention,v);a=node('Transpose',a,perm=[0,2,1,3])
    a=node('Reshape',a,const(np.array([1,197,384],np.int64)))
    x=node('Add',original,linear(a,base+'.attn.proj'));original=x
    a=linear(norm(x,base+'.norm2'),base+'.mlp.fc1')
    gelu=node('Mul',node('Mul',a,const(np.array(.5,np.float32))),node('Add',const(np.array(1.,np.float32)),node('Erf',node('Div',a,const(np.array(np.sqrt(2),np.float32))))))
    x=node('Add',original,linear(gelu,base+'.mlp.fc2'))
x=norm(x,'vit.norm');x=node('Gather',x,const(np.array(0,np.int64)),axis=1);x=linear(x,'vit.head')
nodes.append(h.make_node('Identity',[x],['logits']))
graph=h.make_graph(nodes,'TruthLens official Community Forensics 224',[h.make_tensor_value_info('pixel_values',TensorProto.FLOAT,[1,3,224,224])],[h.make_tensor_value_info('logits',TensorProto.FLOAT,[1,1])],initial)
model=h.make_model(graph,opset_imports=[h.make_opsetid('',17)],producer_name='TruthLens checkpoint exporter');model.ir_version=10
onnx.checker.check_model(model,full_check=True)
out=args.output;onnx.save(model,str(out))
print('Exported',out.stat().st_size,'bytes SHA256',hashlib.sha256(out.read_bytes()).hexdigest(),flush=True)
