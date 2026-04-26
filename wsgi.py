"""WSGI entry point for PythonAnywhere deployment (Plook)."""
import os
import sys

path = os.path.dirname(__file__)
if path not in sys.path:
    sys.path.insert(0, path)
os.chdir(path)

from dotenv import load_dotenv
load_dotenv(os.path.join(path, '.env'))

from flask_plook import plook_app as application
