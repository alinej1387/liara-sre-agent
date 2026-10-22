import asyncio
import os
from dotenv import load_dotenv
import gradio as gr
from langgraph.types import Command
from langchain_core.messages import HumanMessage, SystemMessage

# از agent.py ایمپورت کن
from agent import (
    build_graph, SYSTEM_PROMPT,
    LIARA_API_TOKEN, LIARA_TEAM_ID, LIARA_AI_API_KEY,
)
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

# --- راه‌اندازی یک‌باره MCP و گراف ---
_state = {}

async def setup():
    mcp_env = {
        **os.environ,
        "LIARA_API_TOKEN": LIARA_API_TOKEN,
        "LIARA_MCP_CONSOLIDATED": "false",
    }
    if LIARA_TEAM_ID:
        mcp_env["LIARA_TEAM_ID"] = LIARA_TEAM_ID

    server_params = StdioServerParameters(
        command="npx", args=["-y", "liara-mcp"], env=mcp_env,
    )
    ctx = stdio_client(server_params)
    read, write = await ctx.__aenter__()
    session_ctx = ClientSession(read, write)
    session = await session_ctx.__aenter__()
    await session.initialize()
    mcp_tools = await load_mcp_tools(session)

    llm = ChatOpenAI(
        model="openai/gpt-4o-mini",
        api_key=LIARA_AI_API_KEY,
        base_url="https://ai.liara.ir/api/6ada21b27508e6ca2f0acf7f/v1",
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(mcp_tools)

    graph = build_graph()
    _state.update({
        "graph": graph,
        "config": {"configurable": {
            "llm_with_tools": llm_with_tools,
            "mcp_tools": mcp_tools,
            "thread_id": "ui-session-1",
        }},
        "ctx": ctx,
        "session_ctx": session_ctx,
    })

# --- چت ---
async def chat(user_input, history):
    history = history or []
    history.append((user_input, ""))
    graph = _state["graph"]
    config = _state["config"]

    result = await graph.ainvoke(
        {"messages": [SystemMessage(content=SYSTEM_PROMPT),
                      HumanMessage(content=user_input)]},
        config,
    )

    # اگه interrupt خورد، سؤال تأیید رو نشون بده
    if "__interrupt__" in result:
        intr = result["__interrupt__"][0].value
        msg = f"⚠️ تأیید لازم: عملیات «{intr['action']}» روی {intr['tool']}.\n{intr['reason']}"
        history[-1] = (user_input, msg + "\n\n(برای تأیید «تأیید» و برای رد «رد» را بزنید)")
        _state["pending"] = True
        return history

    history[-1] = (user_input, result["messages"][-1].content)
    return history

async def approve(history):
    if not _state.get("pending"):
        return history
    result = await _state["graph"].ainvoke(
        Command(resume={"approved": True}), _state["config"]
    )
    _state["pending"] = False
    history.append(("تأیید شد ✅", result["messages"][-1].content))
    return history

async def reject(history):
    if not _state.get("pending"):
        return history
    result = await _state["graph"].ainvoke(
        Command(resume={"approved": False}), _state["config"]
    )
    _state["pending"] = False
    history.append(("رد شد ❌", result["messages"][-1].content))
    return history

# --- UI ---
with gr.Blocks(title="Liara SRE Agent") as demo:
    gr.Markdown("# 🤖 Liara SRE Agent\nدستیار هوشمند مدیریت و پایش اپ روی لیارا")
    chatbot = gr.Chatbot(height=500, label="گفتگو")
    with gr.Row():
        msg = gr.Textbox(placeholder="مثلاً: لاگ‌های اپ liara-sre-agent رو بررسی کن", scale=4)
        send = gr.Button("ارسال", scale=1)
    with gr.Row():
        yes = gr.Button("✅ تأیید", variant="primary")
        no = gr.Button("❌ رد", variant="stop")

    send.click(chat, [msg, chatbot], [chatbot]).then(lambda: "", None, msg)
    msg.submit(chat, [msg, chatbot], [chatbot]).then(lambda: "", None, msg)
    yes.click(approve, [chatbot], [chatbot])
    no.click(reject, [chatbot], [chatbot])

if __name__ == "__main__":
    asyncio.run(setup())
    demo.launch()