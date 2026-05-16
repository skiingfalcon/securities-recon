# AgentCore CLI Patterns

## Core Principles

When working with AgentCore CLI for deployment and management:

### Documentation First Approach

- **ALWAYS** search AgentCore and Strands Agents documentation before writing deployment code
- Use `mcp_strands_agents_search_docs` to find AgentCore CLI documentation
- Use `mcp_strands_agents_fetch_doc` to read specific AgentCore pages
- **DO NOT** rely on your own knowledge - consult official docs

### AgentCore CLI Usage

AgentCore CLI is used to deploy and manage Strands Agents on AWS infrastructure.

#### Common Commands

```bash
# Validate configuration file (ALWAYS run after editing agentcore.json)
agentcore validate

# Initialize a new agent project
agentcore init

# Deploy an agent to AWS
agentcore deploy --region us-west-2

# List deployed agents
agentcore list --region us-west-2

# Get agent logs
agentcore logs <agent-name> --region us-west-2

# Delete a deployed agent
agentcore delete <agent-name> --region us-west-2
```

### Project Structure

AgentCore expects a specific project structure:

```
project/
├── agent.py           # Main agent definition
├── requirements.txt   # Python dependencies
├── agentcore.json    # AgentCore configuration (JSON format)
└── tools/            # Custom tool implementations
```

### Configuration File (agentcore.json)

**CRITICAL: Always run `agentcore validate` after editing agentcore.json**

```json
{
  "name": "returns-refunds-agent",
  "runtime": {
    "type": "python3.11",
    "memory": 512,
    "timeout": 300,
    "envVars": [
      { "name": "DYNAMODB_TABLE", "value": "OrdersTable" },
      { "name": "LOG_LEVEL", "value": "INFO" },
      { "name": "AWS_REGION", "value": "us-west-2" }
    ]
  },
  "permissions": [
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:Query"
  ]
}
```

**Important Notes:**
- Configuration file is `agentcore.json` (JSON format, not YAML)
- Environment variables use array format: `"envVars": [{ "name": "KEY", "value": "VALUE" }]`
- **ALWAYS** run `agentcore validate` after making changes to verify configuration
- Validation catches syntax errors and configuration issues before deployment

### Deployment Best Practices

- Always specify `--region us-west-2` explicitly
- **ALWAYS run `agentcore validate` before deployment** to catch configuration errors
- Use environment variables for configuration (array format in agentcore.json)
- Set appropriate memory and timeout values
- Define minimal IAM permissions needed
- Test locally before deploying to AWS
- Validate configuration after any changes to agentcore.json

### Local Testing

```bash
# Test agent locally before deployment
agentcore test --local

# Run agent with specific input
agentcore invoke --local --input '{"query": "process refund for order 12345"}'
```

### Monitoring and Debugging

```bash
# View real-time logs
agentcore logs returns-refunds-agent --follow --region us-west-2

# Get agent metrics
agentcore metrics returns-refunds-agent --region us-west-2

# Check agent status
agentcore status returns-refunds-agent --region us-west-2
```

### CI/CD Integration

- Use AgentCore CLI in deployment pipelines
- Automate testing before deployment
- Implement proper rollback strategies
- Use environment-specific configurations

### Error Handling

- Check CLI exit codes in scripts
- Capture and log CLI output
- Implement retry logic for transient failures
- **Always run `agentcore validate` after editing agentcore.json** to catch errors early
- Validate configuration before deployment
- Use `agentcore validate` in CI/CD pipelines to prevent invalid deployments
