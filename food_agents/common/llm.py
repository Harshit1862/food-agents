import json

from groq import AsyncGroq

from food_agents.common import config

_client: AsyncGroq | None = None


def get_client() -> AsyncGroq:
    """One Groq client per process, created on first use."""
    global _client
    if _client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set. Put it in your .env file.")
        _client = AsyncGroq(api_key=config.GROQ_API_KEY)
    return _client


async def chat_json(*, model: str, system: str, user: str, schema_name: str, schema: dict, effort: str = "low") -> dict:
    """Ask the model for JSON that must match `schema` (Groq 'strict' structured output).

    Strict mode constrains decoding to the schema, so the reply is always valid JSON of
    the right shape. It cannot be combined with tool calling in the same request.
    """
    response = await get_client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        reasoning_effort=effort,
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


async def chat_text(*, model: str, system: str, user: str, effort: str = "low", temperature: float = 0.3) -> str:
    """Plain text completion."""
    response = await get_client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        reasoning_effort=effort,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


async def chat_tools(*, model: str, messages: list[dict], tools: list[dict], effort: str = "medium"):
    """One step of a tool-calling conversation. Returns the model's message: it either has
    `tool_calls` (the model wants tools run) or plain `content` (its answer).

    Tool calling can't be combined with strict structured output, so the arguments the
    model sends are checked by our own code instead.
    """
    response = await get_client().chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        reasoning_effort=effort,
        temperature=0,
    )
    return response.choices[0].message