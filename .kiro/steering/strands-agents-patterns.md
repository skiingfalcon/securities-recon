# Strands Agents SDK Patterns

## Core Principles

When building agents with Strands Agents SDK, follow these patterns and best practices:

### Documentation First Approach

- **ALWAYS** search Strands Agents documentation using MCP tools before writing agent code
- **DO NOT** rely on your own knowledge - use the official documentation
- Use `mcp_strands_agents_search_docs` to find relevant documentation
- Use `mcp_strands_agents_fetch_doc` to read specific documentation pages
- Search for examples and patterns in the documentation

### Tool Decorator Pattern

All agent tools must use the `@tool` decorator pattern from Strands Agents SDK:

```python
from strands_agents import tool

@tool
def process_refund(order_id: str, amount: float, reason: str) -> dict:
    """
    Process a refund for a customer order.
    
    Args:
        order_id: The unique identifier for the order
        amount: The refund amount in USD
        reason: The reason for the refund
        
    Returns:
        dict: Refund confirmation with transaction details
    """
    # Implementation here
    pass
```

### Tool Design Principles

- Each tool should have a single, clear purpose
- Use descriptive names that indicate the tool's function
- Provide comprehensive docstrings with Args and Returns sections
- Include type hints for all parameters and return values
- Keep tools focused and composable

### Agent Configuration

- Define clear agent personas and capabilities
- Set appropriate model parameters (temperature, max_tokens)
- Configure proper error handling and fallback behaviors
- Use structured outputs where applicable

### State Management

- Keep agent state minimal and explicit
- Use conversation context appropriately
- Implement proper state persistence if needed
- Handle state transitions cleanly

### Testing Agents

- Write unit tests for individual tools
- Test agent responses with various inputs
- Validate tool execution and error handling
- Test conversation flows end-to-end
