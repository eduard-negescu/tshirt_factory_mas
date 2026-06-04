---
name: langchain-log-raw-llm-output
description: Capture raw LLM response text before parsing in a LangChain LCEL chain — insert a RunnableLambda logger between the LLM and the output parser so raw output survives parse failures.
source: auto-skill
extracted_at: '2026-06-04T13:01:00.913Z'
---

# Log Raw LLM Output Before Parsing in LangChain

Use this when you have a LangChain LCEL chain that ends with a `PydanticOutputParser`, and you want the raw LLM text saved to logs **before** parsing is attempted. Without this, if the parser fails (bad JSON, wrong structure, `ValidationError`), the raw response is lost — you only get the exception message.

## The problem

A standard LCEL chain:

```python
chain = prompt | llm | parser
response = chain.invoke(prompt_input)
```

If `parser` (a `PydanticOutputParser`) fails, the `AIMessage` content is consumed by the chain and never exposed. The `except` block only sees the exception message, not the malformed JSON that caused it.

## The fix: insert a logging lambda

```python
def _log_raw_response(text) -> str:
    """Log the raw LLM output before any parsing, so it survives parse failures."""
    content = text.content if hasattr(text, "content") else text
    logger.debug("Raw LLM output:\n%s", content)
    return text

chain = prompt | llm | RunnableLambda(_log_raw_response) | parser
```

The lambda is **pass-through** — it logs and returns the input unchanged. Because it sits between the LLM and the parser, the raw output is logged at DEBUG level before parsing is attempted. If parsing fails, the raw text is already in the log file.

## Why DEBUG level

Raw LLM outputs can be large (multi-KB JSON). Logging them at INFO would flood production logs. DEBUG keeps them available for debugging parse failures without noise in normal operation.

## Handling the AIMessage.content attribute

LangChain LLMs (like `ChatOllama`) return `AIMessage` objects. The lambda handles both:

```python
content = text.content if hasattr(text, "content") else text
```

This works whether the chain step returns an `AIMessage` or a plain string (in case the lambda is reused elsewhere).

## Multiple cleaning steps

If your chain already has a `_strip_json_comments` (or similar) step, place the logger **before** it:

```
prompt | llm | _log_raw_response | _strip_json_comments | parser
```

This preserves the truly raw output (with any JSON comments or trailing commas the LLM added). The stripping step then cleans it for the parser.

## What this does NOT cover

- **Network errors** (LLM call fails entirely): The `_log_raw_response` lambda never runs. The existing exception handler already logs the error.
- **Token usage tracking**: This pattern only logs the text response, not token counts. For usage, add a callback or wrap the LLM itself.
- **Streaming**: This pattern works with `invoke()`. For streaming, use an `on_llm_new_token` callback in the LLM's config instead.
