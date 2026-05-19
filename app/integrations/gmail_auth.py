from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"


def has_credentials_file() -> bool:
    return CREDENTIALS_PATH.exists()


def has_gmail_token() -> bool:
    return TOKEN_PATH.exists()


def get_gmail_auth_message() -> str:
    if not has_credentials_file():
        return "credentials.json not found. Please create a Google OAuth client first."
    if not has_gmail_token():
        return "token.json not found. Please complete Google OAuth first."
    return "Gmail authorization found. Authentication successful."