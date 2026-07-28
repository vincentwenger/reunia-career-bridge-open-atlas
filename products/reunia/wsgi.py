"""Production WSGI entry point."""
from dotenv import load_dotenv

load_dotenv()

from meeting_assistant import create_app  # noqa: E402

app = create_app()
