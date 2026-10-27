"""Database session injection for application services."""

from dolphinlab.infrastructure.database import Session


class Service:
    def __init__(self, sessions=Session):
        self.sessions = sessions
