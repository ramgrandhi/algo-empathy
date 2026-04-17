import os
import json
from abc import ABC, abstractmethod
from anthropic import AsyncAnthropic

class LLMProvider(ABC):
    @abstractmethod
    async def chat_stream(self, messages: list[dict], system_prompt: str):
        """
        Yields tokens for SSE.
        messages should be a list of dicts with 'role' and 'content'.
        """
        pass

class ClaudeProvider(LLMProvider):
    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Warning: ANTHROPIC_API_KEY not set")
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = "claude-haiku-4-5" # Using Haiku 3.5 identifier as requested

    async def chat_stream(self, messages: list[dict], system_prompt: str):
        # Anthropic SDK expects messages without system prompt, system prompt is passed separately
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            print(f"ClaudeProvider error: {e}")
            yield "Sorry, I am having trouble connecting to my brain right now."

class AzureOpenAIProvider(LLMProvider):
    def __init__(self):
        # Stub for now
        pass

    async def chat_stream(self, messages: list[dict], system_prompt: str):
        # Return a fixed response as per Phase 4 Acceptance Criteria for the stub
        stub_response = "This is a stub response from the Azure OpenAI provider. I recommend a warm coat."
        
        # Add the <slots> block to satisfy the system prompt requirements and our parser
        slots_json = '{"when": null, "where": null, "how_long": null, "transport": null, "is_recommendation": true}'
        full_response = f"<slots>\n{slots_json}\n</slots>\n{stub_response}"
        
        # Yield in chunks to simulate streaming
        words = full_response.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
            import asyncio
            await asyncio.sleep(0.05)

def get_llm_provider() -> LLMProvider:
    provider_name = os.environ.get("LLM_PROVIDER", "claude").lower()
    if provider_name == "azure":
        return AzureOpenAIProvider()
    return ClaudeProvider()
