"""Abstract base repository for database operations (database-agnostic interface)."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Generic, TypeVar

T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """Abstract base class defining repository interface for any database backend.

    Implementations can be DynamoDB, PostgreSQL, MongoDB, etc.
    This allows switching database backends without changing service layer code.

    Type variable T represents the entity type managed by this repository.
    Concrete implementations specify the entity type (e.g., BaseRepository[Applicant]).
    
    This abstraction provides:
    - Database-agnostic interface for repositories
    - Type safety with generic type parameter
    - Consistency across different entity repositories
    - Easy dependency injection and testing
    """

    @abstractmethod
    def create(self, entity: T) -> str:
        pass

    @abstractmethod
    def get_by_id(self, entity_id: str) -> Optional[T]:
        pass

    @abstractmethod
    def update(self, entity: T) -> None:
        pass

    @abstractmethod
    def delete(self, entity_id: str) -> None:
        pass

    @abstractmethod
    def list_all(self, limit: int = 100) -> List[T]:
        pass

    @abstractmethod
    def search(self, **filters) -> List[T]:
        pass
