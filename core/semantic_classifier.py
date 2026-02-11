"""
MiniLM-based semantic field classification.
Lazy-loads model for lightweight CPU usage.
"""
import logging

logger = logging.getLogger(__name__)

# Canonical descriptions for each field type (used for embedding similarity)
FIELD_DESCRIPTIONS = {
    "first_name": "first name given name",
    "last_name": "last name surname family name",
    "name": "full name contact name customer name your name",
    "email": "email address e-mail mail",
    "phone": "phone number telephone mobile cell contact number",
    "company": "company organization business employer",
    "subject": "subject topic title reason for contact inquiry type",
    "message": "message comment details inquiry your message description",
    "dropdown": "select choose option dropdown",
}

# Fillable types only (exclude captcha, choice, submit, file)
FILLABLE_TYPES = list(FIELD_DESCRIPTIONS.keys())

_model = None
_embeddings_cache = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("MiniLM model loaded for semantic classification")
        except Exception as e:
            logger.warning(f"MiniLM not available, semantic classification disabled: {e}")
    return _model


def _get_embeddings():
    global _embeddings_cache
    if _embeddings_cache is not None:
        return _embeddings_cache
    model = _get_model()
    if model is None:
        return None
    try:
        texts = [FIELD_DESCRIPTIONS[ft] for ft in FILLABLE_TYPES]
        _embeddings_cache = model.encode(texts)
        return _embeddings_cache
    except Exception as e:
        logger.warning(f"Failed to cache embeddings: {e}")
        return None


def classify_semantic(combined_text, min_similarity=0.45):
    """
    Classify field using MiniLM cosine similarity.
    Returns (field_type, confidence) or (None, 0) if no match.
    """
    if not combined_text or not combined_text.strip():
        return None, 0
    model = _get_model()
    if model is None:
        return None, 0
    embeddings = _get_embeddings()
    if embeddings is None:
        return None, 0
    try:
        import numpy as np
        query_emb = model.encode([combined_text.strip()[:512]])[0]
        # Cosine similarity (embeddings are L2-normalized by default)
        sims = np.dot(embeddings, query_emb) / (
            np.linalg.norm(embeddings, axis=1) * np.linalg.norm(query_emb) + 1e-9
        )
        best_idx = int(np.argmax(sims))
        best_sim = float(sims[best_idx])
        if best_sim >= min_similarity:
            return FILLABLE_TYPES[best_idx], min(95, int(best_sim * 100))
    except Exception as e:
        logger.debug(f"Semantic classification failed: {e}")
    return None, 0
