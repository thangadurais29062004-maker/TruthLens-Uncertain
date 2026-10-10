`c2pa-untrusted.jpg` is the C.jpg test fixture from the Apache-2.0 contentauth/c2pa-python repository, downloaded 2026-10-09:
https://github.com/contentauth/c2pa-python/blob/main/tests/fixtures/C.jpg

It contains a valid cryptographic manifest signed with an untrusted test certificate. The regression test verifies that untrusted is distinguished from tampered. Altering a byte of the signed asset should produce an invalid state. It is a software fixture, not a detector benchmark.
