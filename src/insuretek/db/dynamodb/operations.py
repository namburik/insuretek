"""InsureTek-specific DynamoDB operations for applicant data."""

from typing import List, Dict, Any
from boto3.dynamodb.conditions import Key
from mltek.db.dynamodb.dynamodb_operations import DynamoDBOperations, _filter_active


class ApplicantOperations(DynamoDBOperations):
    """InsureTek applicant-specific DynamoDB query operations."""

    def query_by_applicant(self, applicant_id: str) -> List[Dict[str, Any]]:
        """Query all items for a specific applicant by applicant_id."""
        return self.query(Key("applicant_id").eq(applicant_id))

    def get_active_by_applicant(self, applicant_id: str) -> List[Dict[str, Any]]:
        """Query all active items for a specific applicant."""
        items = self.query_by_applicant(applicant_id)
        return _filter_active(items)


__all__ = ["ApplicantOperations"]
