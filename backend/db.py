# ---------- backend/db.py ----------
"""
Database helpers: engine, session factory, and convenience CRUD functions.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
import os
from .models import Base, Topic, Subtopic, Note, SearchCache, MCPEvent
import hashlib

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./data/research_agent.db')

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


def init_db():
    Base.metadata.create_all(bind=engine)

# CRUD helpers

def create_topic(title: str, db=None):
    db = db or SessionLocal()
    topic = Topic(title=title)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def create_subtopic(topic_id: int, title: str, db=None):
    db = db or SessionLocal()
    sub = Subtopic(topic_id=topic_id, title=title)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def save_note(subtopic_id: int, source_title: str, source_url: str, content: str, extracted_summary: str, db=None):
    db = db or SessionLocal()
    h = hashlib.sha256((source_url or '')[:2000].encode('utf-8') + content[:20000].encode('utf-8')).hexdigest()
    # check duplicate
    existing = db.query(Note).filter_by(content_hash=h).first()
    if existing:
        return existing
    note = Note(subtopic_id=subtopic_id, source_title=source_title, source_url=source_url, content=content, extracted_summary=extracted_summary, content_hash=h)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def get_notes_for_subtopic(subtopic_id: int, db=None):
    db = db or SessionLocal()
    return db.query(Note).filter_by(subtopic_id=subtopic_id).order_by(Note.created_at.desc()).all()


def cache_search_results(query: str, results_json: str, db=None):
    db = db or SessionLocal()
    sc = SearchCache(query=query, results_json=results_json)
    db.add(sc)
    db.commit()
    db.refresh(sc)
    return sc


def get_cached_search(query: str, db=None):
    db = db or SessionLocal()
    return db.query(SearchCache).filter_by(query=query).first()


def log_mcp_event(endpoint: str, request_json: str, response_json: str, topic_id: int = None, db=None):
    db = db or SessionLocal()
    ev = MCPEvent(endpoint=endpoint, request_json=request_json, response_json=response_json, topic_id=topic_id)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev
# ---------- end of backend/db.py ----------