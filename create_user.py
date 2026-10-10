"""Create an account on a deployment with self-registration disabled."""
import getpass
from server import database
from truthlens.auth import create_user

username=input('Username: ').strip()
password=getpass.getpass('Password (at least 12 characters): ')
if password!=getpass.getpass('Confirm password: '): raise SystemExit('Passwords differ')
with database() as db: user=create_user(db,username,password)
print('Account created:',user['username'])
