"""
FLOW-FORGE Topic Deduplication
Jaccard similarity-based deduplication logic.
Per Canon § 1.2: Research with De-duplication
"""

from typing import Set, Tuple, Optional, List, Dict, Any
from app.core.logging import get_logger

logger = get_logger(__name__)


def tokenize(text: str) -> Set[str]:
    """
    Tokenize text into lowercase words.
    Remove punctuation and split on whitespace.
    """
    # Simple tokenization: lowercase, remove punctuation, split
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    tokens = set(text.split())
    return tokens


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """
    Calculate Jaccard similarity between two sets.
    Jaccard = |A ∩ B| / |A ∪ B|
    Returns value between 0 (no overlap) and 1 (identical).
    """
    if not set1 or not set2:
        return 0.0
    
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    
    if union == 0:
        return 0.0
    
    return intersection / union


def calculate_topic_similarity(
    title1: str,
    rotation1: str,
    cta1: str,
    title2: str,
    rotation2: str,
    cta2: str
) -> float:
    """
    Calculate overall similarity between two topics.
    Uses weighted Jaccard similarity across title, rotation, and CTA.
    
    Weights:
    - Title: 0.5 (most important)
    - Rotation: 0.3
    - CTA: 0.2
    """
    # Tokenize all fields
    title1_tokens = tokenize(title1)
    rotation1_tokens = tokenize(rotation1)
    cta1_tokens = tokenize(cta1)
    
    title2_tokens = tokenize(title2)
    rotation2_tokens = tokenize(rotation2)
    cta2_tokens = tokenize(cta2)
    
    # Calculate Jaccard similarities
    title_sim = jaccard_similarity(title1_tokens, title2_tokens)
    rotation_sim = jaccard_similarity(rotation1_tokens, rotation2_tokens)
    cta_sim = jaccard_similarity(cta1_tokens, cta2_tokens)
    
    # Weighted average
    weighted_sim = (
        title_sim * 0.5 +
        rotation_sim * 0.3 +
        cta_sim * 0.2
    )
    
    logger.debug(
        "similarity_calculated",
        title_sim=title_sim,
        rotation_sim=rotation_sim,
        cta_sim=cta_sim,
        weighted_sim=weighted_sim
    )
    
    return weighted_sim


def is_duplicate_topic(
    title: str,
    rotation: str,
    cta: str,
    existing_topics: List[Dict[str, Any]],
    threshold: float = 0.7
) -> Tuple[bool, Optional[str], float]:
    """
    Check if a topic is a duplicate of existing topics.
    
    Args:
        title: New topic title
        rotation: New topic rotation
        cta: New topic CTA
        existing_topics: List of existing topics from database
        threshold: Similarity threshold (default 0.7)
    
    Returns:
        Tuple of (is_duplicate, matched_topic_id, max_similarity)
    """
    max_similarity = 0.0
    matched_topic_id = None
    
    for existing in existing_topics:
        similarity = calculate_topic_similarity(
            title, rotation, cta,
            existing["title"], existing["rotation"], existing["cta"]
        )
        
        if similarity > max_similarity:
            max_similarity = similarity
            matched_topic_id = existing["id"]
        
        # Early exit if we find a clear duplicate
        if similarity >= threshold:
            logger.info(
                "duplicate_topic_found",
                similarity=similarity,
                matched_topic_id=matched_topic_id,
                new_title=title[:50],
                existing_title=existing["title"][:50]
            )
            return True, matched_topic_id, similarity
    
    logger.info(
        "topic_uniqueness_check",
        is_duplicate=False,
        max_similarity=max_similarity,
        title=title[:50]
    )
    
    return False, matched_topic_id, max_similarity


def deduplicate_topics(
    new_topics: List[Dict[str, str]],
    existing_topics: List[Dict[str, Any]],
    threshold: float = 0.7
) -> List[Dict[str, str]]:
    """
    Filter out duplicate topics from a list of new topics.
    
    Args:
        new_topics: List of new topics to check
        existing_topics: List of existing topics from database
        threshold: Similarity threshold
    
    Returns:
        List of unique topics (non-duplicates)
    """
    unique_topics = []
    
    for topic in new_topics:
        is_dup, _, similarity = is_duplicate_topic(
            topic["title"],
            topic["rotation"],
            topic["cta"],
            existing_topics + unique_topics,  # Check against both existing and already-added unique
            threshold
        )
        
        if not is_dup:
            unique_topics.append(topic)
        else:
            logger.info(
                "topic_filtered_as_duplicate",
                title=topic["title"][:50],
                similarity=similarity
            )
    
    logger.info(
        "deduplication_complete",
        input_count=len(new_topics),
        output_count=len(unique_topics),
        filtered_count=len(new_topics) - len(unique_topics)
    )
    
    return unique_topics
