"""Unstructured retrieval (spec 3.3) with LangChain: loaders -> RecursiveCharacterTextSplitter -> embeddings -> Chroma.

Everything tunable (folders, chunk size, top_k, models) comes from config.yaml. The knowledge/ folder is read as-is:
.md/.txt files with TextLoader, .csv files with CSVLoader (one document per row).
LangChain imports are lazy so the MCP servers (which only need excerpts) start fast.
"""
import os
import shutil
from pathlib import Path

from config import ROOT, load_config
from guardrails import redact_for_logging
from logging_config import get_logger, trace

logger = get_logger("knowledge")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def demo_enabled():
    """NEXUS_DEMO=1 also indexes/searches the deliberately poisoned demo documents."""
    return os.environ.get("NEXUS_DEMO", "") == "1"


@trace(logger, redact=_r)
def knowledge_folders(include_demo=False):
    """[(category, Path)] of the folders to index; missing folders are skipped."""
    cfg = load_config()["knowledge"]
    folders = [(name, ROOT / cfg["root"] / name) for name in cfg["folders"]]
    if include_demo:
        folders.append(("demo", ROOT / cfg["demo_dir"]))
    return [(name, path) for name, path in folders if path.is_dir()]


@trace(logger, redact=_r)
def index_path(include_demo=False):
    """Chroma persist directory (the demo index is kept separate from the normal one)."""
    base = ROOT / load_config()["knowledge"]["index_dir"]
    return Path(str(base) + ("_demo" if include_demo else ""))


@trace(logger, redact=_r)
def make_embeddings():
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=load_config()["llm"]["embedding"])


@trace(logger, redact=_r)
def load_documents(include_demo=False):
    """Load every knowledge file with LangChain loaders and tag each document with doc_id and category."""
    from langchain_community.document_loaders import CSVLoader, DirectoryLoader, TextLoader
    cfg = load_config()["knowledge"]
    documents = []
    for category, folder in knowledge_folders(include_demo):
        for extension in cfg["text_extensions"]:
            loader = DirectoryLoader(str(folder), glob=f"**/*{extension}", loader_cls=TextLoader,
                                     loader_kwargs={"encoding": "utf-8"})
            documents += _tag(loader.load(), category)
        for extension in cfg["csv_extensions"]:
            loader = DirectoryLoader(str(folder), glob=f"**/*{extension}", loader_cls=CSVLoader,
                                     loader_kwargs={"encoding": "utf-8"})
            documents += _tag(loader.load(), category)
    return documents


@trace(logger, redact=_r)
def _tag(documents, category):
    for document in documents:
        document.metadata["doc_id"] = Path(document.metadata["source"]).stem
        document.metadata["category"] = category
    return documents


@trace(logger, redact=_r)
def split_documents(documents):
    """Chunking is done by LangChain's RecursiveCharacterTextSplitter (sizes from config)."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    cfg = load_config()["knowledge"]
    splitter = RecursiveCharacterTextSplitter(chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"])
    return splitter.split_documents(documents)


@trace(logger, redact=_r)
def build_index(include_demo=False, embeddings=None):
    """(Re)build the Chroma vector index. `embeddings` can be injected (tests); default = OpenAI from config."""
    from langchain_chroma import Chroma
    chunks = split_documents(load_documents(include_demo))
    target = index_path(include_demo)
    if target.exists():
        shutil.rmtree(target)
    Chroma.from_documents(documents=chunks, embedding=embeddings or make_embeddings(), persist_directory=str(target))
    return {"documents": len({c.metadata["doc_id"] for c in chunks}), "chunks": len(chunks), "path": str(target)}


@trace(logger, redact=_r)
def get_retriever(include_demo=False, embeddings=None):
    from langchain_chroma import Chroma
    store = Chroma(persist_directory=str(index_path(include_demo)), embedding_function=embeddings or make_embeddings())
    return store.as_retriever(search_kwargs={"k": load_config()["knowledge"]["top_k"]})


@trace(logger, redact=_r)
def search(query, include_demo=False, embeddings=None):
    """Semantic search. Results are UNTRUSTED text - callers must sanitise them before use."""
    docs = get_retriever(include_demo, embeddings).invoke(query)
    return [{"doc_id": d.metadata.get("doc_id"), "category": d.metadata.get("category"), "text": d.page_content} for d in docs]


@trace(logger, redact=_r)
def find_document(doc_id):
    """Path of the knowledge file whose name (without extension) is doc_id, or None."""
    for _category, folder in knowledge_folders(include_demo=True):
        for candidate in sorted(folder.rglob(f"{doc_id}.*")):
            if candidate.is_file():
                return candidate
    return None
