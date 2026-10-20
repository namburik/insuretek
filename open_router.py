import os
import asyncio
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from shared.common.agents.bridge import HumanMessage

def setup_open_router_agent():
    """
    Set up a LangChain ReAct agent that calls Open Router API.
    
    Returns:
        AgentExecutor: The configured agent executor
    """
    # Get API key from environment variable
    api_key = os.getenv("OPEN_ROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPEN_ROUTER_API_KEY environment variable not set")
    
    # Initialize the Open Router LLM using OpenAI-compatible API
    llm = ChatOpenAI(
        model="nvidia/nemotron-3-super-120b-a12b:free",
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.7,
        max_tokens=1000,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False
            }
        }
    )
    
    # Define tools for the agent
    @tool
    def answer_question(question: str) -> str:
        """Answer a question by reasoning and thinking through it."""
        return question
    
    tools = [answer_question]
    
    system_prompt = "You are a helpful assistant. Answer the user's question accurately and directly."
    
    # Create the ReAct agent with tools
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)
    
    
    return agent

async def call_open_router(prompt: str) -> str:
    """
    Call Open Router LLM API using a LangChain ReAct agent.
    
    Args:
        prompt: The prompt to send to the LLM
        
    Returns:
        The LLM's response as a string
    """
    agent = setup_open_router_agent()
    result = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]})
    
    # Handle different possible response structures
    if isinstance(result, dict):
        if "output" in result:
            return result["output"]
        elif "messages" in result and result["messages"]:
            # If it's a message structure, get the last message content
            last_message = result["messages"][-1]
            if hasattr(last_message, "content"):
                return last_message.content
            return str(last_message)
        else:
            return str(result)
    else:
        return str(result)

if __name__ == "__main__":
    # Test with the weather prompt
    prompt = "Who is the president of the United States?"
    
    async def main():
        try:
            response = await call_open_router(prompt)
            print(f"Prompt: {prompt}")
            print(f"Response: {response}")
        except Exception as e:
            print(f"Error calling Open Router: {e}")
    
    asyncio.run(main())