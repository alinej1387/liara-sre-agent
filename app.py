import asyncio
import os
from dotenv import load_dotenv
import streamlit as st
from langgraph.types import Command
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent import build_graph, SYSTEM_PROMPT, LIARA_API_TOKEN, LIARA_TEAM_ID, LIARA_AI_API_KEY

load_dotenv()

# ---------- event loop پایدار ----------
if "loop" not in st.session_state:
    st.session_state["loop"] = asyncio.new_event_loop()
    asyncio.set_event_loop(st.session_state["loop"])
loop = st.session_state["loop"]

# ---------- MCP session پایدار ----------
def _mcp_env():
    env = {
        **os.environ,
        "LIARA_API_TOKEN": LIARA_API_TOKEN,
        "LIARA_MCP_CONSOLIDATED": "false",
    }
    if LIARA_TEAM_ID:
        env["LIARA_TEAM_ID"] = LIARA_TEAM_ID
    return env

async def _setup_mcp():
    server_params = StdioServerParameters(
        command="npx", args=["-y", "liara-mcp"], env=_mcp_env(),
    )
    ctx = stdio_client(server_params)
    read, write = await ctx.__aenter__()
    session_ctx = ClientSession(read, write)
    session = await session_ctx.__aenter__()
    await session.initialize()
    mcp_tools = await load_mcp_tools(session)
    return {"ctx": ctx, "session_ctx": session_ctx, "mcp_tools": mcp_tools}

if "mcp_ready" not in st.session_state:
    st.session_state["mcp_ready"] = False

if not st.session_state["mcp_ready"]:
    with st.spinner("در حال اتصال به لیارا..."):
        st.session_state["mcp"] = loop.run_until_complete(_setup_mcp())
    st.session_state["mcp_ready"] = True
    st.success("اتصال برقرار شد ✅")

mcp = st.session_state["mcp"]

# ---------- مدل با هدر Connection: close ----------
if "llm" not in st.session_state:
    st.session_state["llm"] = ChatOpenAI(
        model="openai/gpt-4o-mini",
        api_key=LIARA_AI_API_KEY,
        base_url="https://ai.liara.ir/api/6ada21b27508e6ca2f0acf7f/v1",
        temperature=0,
        default_headers={"Connection": "close"},
    )

llm_with_tools = st.session_state["llm"].bind_tools(mcp["mcp_tools"])

# ---------- گراف پایدار (با MemorySaver) ----------
if "graph" not in st.session_state:
    st.session_state["graph"] = build_graph()
graph = st.session_state["graph"]

# ---------- اجرا ----------
async def _run(messages=None, resume=None):
    config = {"configurable": {
        "llm_with_tools": llm_with_tools,
        "mcp_tools": mcp["mcp_tools"],
        "thread_id": "streamlit-session",
    }}
    if resume is not None:
        return await graph.ainvoke(Command(resume={"approved": resume}), config)
    return await graph.ainvoke({"messages": messages}, config)

# ---------- UI ----------
st.set_page_config(page_title="Liara SRE Agent", page_icon="🤖")
st.title("🤖 Liara SRE Agent")
st.caption("دستیار هوشمند مدیریت و پایش اپ روی لیارا — با Human-in-the-Loop")

if "messages" not in st.session_state:
    st.session_state["messages"] = []
    st.session_state["pending"] = None

for role, msg in st.session_state["messages"]:
    with st.chat_message(role):
        st.markdown(msg)

if prompt := st.chat_input("درخواستت چیه؟"):
    st.session_state["messages"].append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("در حال پردازش..."):
        result = loop.run_until_complete(_run(messages=[
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]))

    if "__interrupt__" in result:
        intr = result["__interrupt__"][0].value
        st.session_state["pending"] = intr
        with st.chat_message("assistant"):
            st.warning(f"⚠️ تأیید لازم: عملیات «{intr['action']}» روی {intr['tool']}\n\n{intr['reason']}")
    else:
        answer = result["messages"][-1].content
        st.session_state["messages"].append(("assistant", answer))
        with st.chat_message("assistant"):
            st.markdown(answer)

if st.session_state.get("pending"):
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ تأیید", type="primary", use_container_width=True):
            with st.spinner("در حال اجرا..."):
                result = loop.run_until_complete(_run(resume=True))
            answer = result["messages"][-1].content
            st.session_state["messages"].append(("assistant", f"✅ تأیید شد\n\n{answer}"))
            st.session_state["pending"] = None
            st.rerun()
    with col2:
        if st.button("❌ رد", use_container_width=True):
            with st.spinner("در حال لغو..."):
                result = loop.run_until_complete(_run(resume=False))
            answer = result["messages"][-1].content
            st.session_state["messages"].append(("assistant", f"❌ رد شد\n\n{answer}"))
            st.session_state["pending"] = None
            st.rerun()