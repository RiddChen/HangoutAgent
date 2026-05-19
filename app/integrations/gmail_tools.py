from langchain_google_community import GmailToolkit


def get_gmail_tools():
    toolkit = GmailToolkit()
    return toolkit.get_tools()