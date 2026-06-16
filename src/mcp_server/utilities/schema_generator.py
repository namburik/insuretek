"""
Auto-generate JSON schemas from Python dataclasses.

This module eliminates duplication by generating JSON schemas directly
from dataclass definitions in common/model_children.py.

Single source of truth: the dataclasses themselves.
"""

from typing import get_type_hints, get_origin, get_args, Dict, Any, Optional
from dataclasses import fields, is_dataclass


# Type mapping for Python to JSON schema
TYPE_TO_JSON_SCHEMA = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    Dict: "object",
}


def is_optional_type(python_type) -> bool:
    """Check if a type is Optional (Union with None)."""
    origin = get_origin(python_type)
    if origin is Optional:
        return True
    if hasattr(python_type, '__args__'):
        return type(None) in get_args(python_type)
    return False


def unwrap_optional(python_type):
    """Extract the inner type from Optional[T]."""
    origin = get_origin(python_type)
    if origin is Optional or is_optional_type(python_type):
        args = get_args(python_type)
        return args[0] if args else python_type
    return python_type


def get_inner_type(field_type):
    """Extract inner type from Optional, List, etc."""
    args = get_args(field_type)
    return args[0] if args else field_type


def python_to_json_type(python_type) -> str:
    """Convert Python type to JSON schema type."""
    # Unwrap Optional[T] -> T
    python_type = unwrap_optional(python_type)
    
    # Check for List[T]
    if get_origin(python_type) is list:
        return "array"
    
    # Check type mapping
    if python_type in TYPE_TO_JSON_SCHEMA:
        return TYPE_TO_JSON_SCHEMA[python_type]
    
    # Default to object for dataclasses
    if is_dataclass(python_type):
        return "object"
    
    return "object"


def get_field_description(field_name: str, field_metadata: dict = None) -> str:
    """Get description for a field from metadata or auto-generate from field name."""
    # Check field metadata first
    if field_metadata and "description" in field_metadata:
        return field_metadata["description"]
    
    # Auto-generate description from field name
    words = field_name.replace("_", " ").split()
    return " ".join(word.capitalize() if word.lower() not in ['indicator', 'code'] else word for word in words)


def dataclass_to_json_schema(dataclass_type, description: str = None) -> Dict[str, Any]:
    """
    Generate JSON schema from a Python dataclass.
    
    Args:
        dataclass_type: A Python dataclass
        description: Optional description for the schema
    
    Returns:
        JSON schema as dictionary
    """
    if not is_dataclass(dataclass_type):
        raise ValueError(f"{dataclass_type} is not a dataclass")
    
    type_hints = get_type_hints(dataclass_type)
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": {}
    }
    
    if description:
        schema["description"] = description
    
    # Track which fields are required
    required_fields = []
    
    for field in fields(dataclass_type):
        field_name = field.name
        field_type = type_hints.get(field_name, field.type)
        
        # Check if field is Optional
        field_is_optional = is_optional_type(field_type)
        
        # Track required fields
        if field.default is field.default_factory and not field_is_optional:
            required_fields.append(field_name)
        
        # Build field schema
        field_schema: Dict[str, Any] = {}
        
        # Get JSON type
        json_type = python_to_json_type(field_type)
        field_schema["type"] = json_type
        
        # Add description (check metadata first)
        field_schema["description"] = get_field_description(field_name, field.metadata if field.metadata else None)
        
        # Handle nested dataclasses
        if json_type == "object" and get_origin(field_type) is not list:
            inner_type = get_inner_type(field_type)
            if is_dataclass(inner_type):
                nested_schema = dataclass_to_json_schema(inner_type)
                field_schema["properties"] = nested_schema.get("properties", {})
        
        # Handle arrays
        if json_type == "array":
            item_type = get_inner_type(field_type)
            field_schema["items"] = {"type": python_to_json_type(item_type)}
        
        schema["properties"][field_name] = field_schema
    
    if required_fields:
        schema["required"] = required_fields
    
    return schema


# Cache generated schemas to avoid regenerating
_schema_cache = {}


def get_cached_schema(dataclass_type) -> Dict[str, Any]:
    """Get cached schema, generating if needed."""
    cache_key = dataclass_type.__name__
    
    if cache_key not in _schema_cache:
        _schema_cache[cache_key] = dataclass_to_json_schema(dataclass_type)
    
    return _schema_cache[cache_key]
