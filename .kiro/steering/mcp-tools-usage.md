# MCP Tools Usage Guidelines

## Core Principle

**ALWAYS use MCP tools to look up documentation before writing code. DO NOT rely on your own knowledge.**

## Available MCP Tools

### AWS Documentation Tools

#### Search AWS Documentation
```
mcp_awslabsaws_documentation_mcp_server_search_documentation
```
Use this to find relevant AWS documentation pages.

**When to use:**
- Before implementing any AWS service integration
- When you need to understand AWS service capabilities
- To find current API references and best practices

**Example:**
```
Search for: "Lambda timeout configuration"
Search for: "DynamoDB query patterns"
Search for: "S3 presigned URLs"
```

#### Read AWS Documentation
```
mcp_awslabsaws_documentation_mcp_server_read_documentation
```
Use this to read specific AWS documentation pages.

**When to use:**
- After finding relevant pages via search
- To get detailed implementation guidance
- To understand specific service features

#### Read Specific Sections
```
mcp_awslabsaws_documentation_mcp_server_read_sections
```
Use this to read targeted sections from AWS docs.

**When to use:**
- When you need specific information from a known page
- To avoid reading entire lengthy documents
- For focused implementation guidance

### Strands Agents Documentation Tools

#### Search Strands Agents Docs
```
mcp_strands_agents_search_docs
```
Use this to find relevant Strands Agents documentation.

**When to use:**
- Before writing agent code
- When implementing tools with @tool decorator
- To understand agent configuration options
- Before using AgentCore CLI commands

**Example:**
```
Search for: "tool decorator pattern"
Search for: "agent configuration"
Search for: "agentcore deploy"
```

#### Fetch Strands Agents Documentation
```
mcp_strands_agents_fetch_doc
```
Use this to read specific Strands Agents documentation pages.

**When to use:**
- After finding relevant pages via search
- To get detailed API references
- To understand implementation patterns
- For AgentCore CLI command details

## Workflow for Writing Code

### Step 1: Search Documentation
Before writing any code, search for relevant documentation:

```
1. Identify what you need to implement
2. Search AWS docs if using AWS services
3. Search Strands docs if writing agent code
4. Review search results for relevant pages
```

### Step 2: Read Documentation
Read the specific documentation pages:

```
1. Use read_documentation or fetch_doc tools
2. Focus on relevant sections
3. Note important patterns and examples
4. Identify required parameters and configurations
```

### Step 3: Implement Code
Only after consulting documentation:

```
1. Write code following documented patterns
2. Use type hints and educational comments
3. Include references to documentation in comments
4. Test implementation against documented behavior
```

## Example Workflow

### Implementing a Lambda Function with DynamoDB

**Step 1: Search**
```
Search AWS docs: "Lambda DynamoDB integration best practices"
Search AWS docs: "DynamoDB Python boto3 examples"
```

**Step 2: Read**
```
Read sections on:
- Lambda environment variables
- DynamoDB client configuration
- Error handling patterns
- IAM permissions required
```

**Step 3: Implement**
```python
# Based on AWS documentation:
# https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html
# https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GettingStarted.Python.html

import boto3
from typing import Dict, Optional

# Initialize DynamoDB client with us-west-2 region as per project standards
dynamodb = boto3.client('dynamodb', region_name='us-west-2')

def get_order(order_id: str) -> Optional[Dict]:
    """
    Retrieve order details from DynamoDB.
    
    Uses consistent reads to ensure we have the latest data
    before processing returns/refunds.
    """
    # Implementation based on AWS documentation
    pass
```

### Implementing a Strands Agent Tool

**Step 1: Search**
```
Search Strands docs: "tool decorator pattern"
Search Strands docs: "tool error handling"
```

**Step 2: Read**
```
Read documentation on:
- @tool decorator usage
- Tool function signatures
- Return value formats
- Error handling patterns
```

**Step 3: Implement**
```python
# Based on Strands Agents documentation
from strands_agents import tool
from typing import Dict

@tool
def check_return_eligibility(order_id: str) -> Dict[str, any]:
    """
    Check if an order is eligible for return.
    
    Implements the @tool decorator pattern from Strands Agents SDK
    as documented in the official guide.
    """
    # Implementation based on Strands documentation
    pass
```

## Documentation References in Code

Always include documentation references in comments:

```python
# AWS Lambda timeout limit is 15 minutes (900 seconds)
# Reference: https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html
LAMBDA_TIMEOUT = 900

# Strands @tool decorator automatically handles serialization
# Reference: Strands Agents SDK - Tool Development Guide
@tool
def process_refund(amount: float) -> Dict:
    pass
```

## When NOT to Use MCP Tools

Only skip MCP tools for:
- Standard Python syntax and built-in functions
- Basic programming concepts
- Project-specific business logic
- Local utility functions

For everything AWS or Strands Agents related: **USE MCP TOOLS FIRST**.
