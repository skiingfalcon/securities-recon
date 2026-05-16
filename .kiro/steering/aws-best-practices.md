# AWS Best Practices and Documentation

## Core Principles

When working with AWS services in this project, always follow these guidelines:

### Documentation First Approach

- **ALWAYS** search and consult AWS documentation using MCP tools before writing code
- **DO NOT** rely on your own knowledge - AWS services evolve rapidly
- Use `mcp_awslabsaws_documentation_mcp_server_search_documentation` to find relevant docs
- Use `mcp_awslabsaws_documentation_mcp_server_read_documentation` to read specific pages
- Use `mcp_awslabsaws_documentation_mcp_server_read_sections` for targeted information

### Region Configuration

- **ALL** AWS operations must target the `us-west-2` region
- Always specify region explicitly in code and CLI commands
- Example: `--region us-west-2` for CLI commands
- Example: `region_name='us-west-2'` for boto3 clients

### CLI Best Practices

- **ALWAYS** include `--no-cli-pager` when executing AWS CLI commands from terminal
- This prevents interactive pagers from blocking automation
- Example: `aws lambda list-functions --region us-west-2 --no-cli-pager`

### Security Best Practices

- Use IAM roles and policies with least privilege
- Never hardcode credentials in code
- Use environment variables or AWS credentials file
- Enable encryption at rest and in transit where applicable
- Use AWS Secrets Manager for sensitive data

### Cost Optimization

- Clean up unused resources
- Use appropriate instance/function sizing
- Implement proper error handling to avoid retry storms
- Set appropriate timeouts for Lambda functions

### Error Handling

- Implement proper exception handling for all AWS API calls
- Use exponential backoff for retries
- Log errors with sufficient context for debugging
- Handle throttling and rate limiting gracefully
