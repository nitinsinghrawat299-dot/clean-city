"""
Run this once to create the ADMIN_PASSWORD_HASH value for your .env file.

Usage:
    python generate_password_hash.py
"""

from getpass import getpass
from werkzeug.security import generate_password_hash


password = getpass("Choose an admin password: ")

confirm = getpass("Confirm password: ")

if password != confirm:
    print("Passwords did not match. Run the script again.")

else:
    print("\nAdd this line to your .env file:\n")
    print(f"ADMIN_PASSWORD_HASH={generate_password_hash(password)}")
