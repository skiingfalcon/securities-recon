# Python Code Standards

## Core Principles

All Python code in this project must follow these standards:

### Type Hints

- **ALWAYS** use type hints for function parameters and return values
- Use `typing` module for complex types (List, Dict, Optional, Union, etc.)
- Type hints improve code clarity and enable better IDE support

```python
from typing import List, Dict, Optional, Union

def calculate_refund(
    order_total: float,
    items_returned: List[str],
    refund_policy: Dict[str, any]
) -> Optional[float]:
    """Calculate refund amount based on policy."""
    pass
```

### Educational Inline Comments

- Include inline comments that explain **why**, not just **what**
- Comments should be educational for someone learning the codebase
- Explain business logic, AWS service interactions, and design decisions
- Use comments to highlight important patterns or gotchas

```python
# Query DynamoDB for order details
# We use consistent reads to ensure we have the latest order status
# before processing the refund to avoid race conditions
response = dynamodb.get_item(
    TableName='Orders',
    Key={'order_id': {'S': order_id}},
    ConsistentRead=True  # Ensures strong consistency
)
```

### Code Organization

- Keep functions small and focused (single responsibility)
- Use meaningful variable and function names
- Group related functionality into modules
- Follow PEP 8 style guidelines

### Minimal and Focused Code

- Write only the code necessary to demonstrate core concepts
- Avoid over-engineering or premature optimization
- Keep examples clear and easy to understand
- Remove unused imports and dead code

### Error Handling

```python
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

def process_return(order_id: str) -> Optional[Dict[str, any]]:
    """
    Process a return request for an order.
    
    Args:
        order_id: The unique order identifier
        
    Returns:
        Optional[Dict]: Return confirmation or None if failed
    """
    try:
        # Validate order exists and is eligible for return
        order = get_order(order_id)
        if not order:
            logger.warning(f"Order {order_id} not found")
            return None
            
        # Process the return logic here
        return {"status": "success", "order_id": order_id}
        
    except Exception as e:
        # Log the error with context for debugging
        logger.error(f"Failed to process return for {order_id}: {str(e)}")
        return None
```

### Documentation

- Every function must have a docstring
- Use Google or NumPy docstring format
- Include Args, Returns, and Raises sections
- Provide usage examples for complex functions

### Imports

- Group imports: standard library, third-party, local
- Use absolute imports
- Avoid wildcard imports (`from module import *`)

```python
# Standard library
import logging
from typing import Dict, List, Optional

# Third-party
import boto3
from strands_agents import tool, Agent

# Local
from .models import Order, RefundRequest
```
