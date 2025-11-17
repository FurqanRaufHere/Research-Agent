# ---------- backend/models.py ----------
"""
SQLAlchemy models for topics, subtopics, notes, search_cache, and mcp_events.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Topic(Base):
    __tablename__ = 'topics'
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    subtopics = relationship('Subtopic', back_populates='topic')

class Subtopic(Base):
    __tablename__ = 'subtopics'
    id = Column(Integer, primary_key=True)
    topic_id = Column(Integer, ForeignKey('topics.id'), index=True)
    title = Column(String, nullable=False)
    status = Column(String, default='created')
    created_at = Column(DateTime, default=datetime.utcnow)
    topic = relationship('Topic', back_populates='subtopics')
    notes = relationship('Note', back_populates='subtopic')

    __table_args__ = (
        UniqueConstraint('topic_id', 'title', name='uq_subtopic_topic_title'),
    )

class Note(Base):
    __tablename__ = 'notes'
    id = Column(Integer, primary_key=True)
    subtopic_id = Column(Integer, ForeignKey('subtopics.id'), index=True)
    source_title = Column(String)
    source_url = Column(String)
    content = Column(Text)
    extracted_summary = Column(Text)
    content_hash = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    subtopic = relationship('Subtopic', back_populates='notes')

    __table_args__ = (
        UniqueConstraint('content_hash', name='uq_note_hash'),
    )

class SearchCache(Base):
    __tablename__ = 'search_cache'
    id = Column(Integer, primary_key=True)
    query = Column(String, unique=True, index=True)
    results_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class MCPEvent(Base):
    __tablename__ = 'mcp_events'
    id = Column(Integer, primary_key=True)
    endpoint = Column(String)
    request_json = Column(Text)
    response_json = Column(Text)
    topic_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
# ---------- end of backend/models.py ----------