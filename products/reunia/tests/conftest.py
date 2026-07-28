import pytest

from meeting_assistant import create_app


@pytest.fixture()
def app():
    return create_app("testing")
