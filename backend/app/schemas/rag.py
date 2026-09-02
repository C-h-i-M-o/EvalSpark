from pydantic import Field, StrictInt

from app.schemas.knowledge_base import KnowledgeSchema, SourceLocation


class RagEvidence(KnowledgeSchema):
    label: str = Field(pattern=r"^S[1-5]$")
    document_id: StrictInt = Field(gt=0)
    document_name: str
    chunk_id: str
    index_revision: StrictInt = Field(gt=0)
    text: str
    similarity: float = Field(allow_inf_nan=False)
    source: SourceLocation
