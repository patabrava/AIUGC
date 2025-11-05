"""
FLOW-FORGE Topic Discovery Agents
LLM agents for topic research and extraction.
Per Canon § 6: LLM Agents
"""

from typing import List, Dict, Any
from app.adapters.llm_client import get_llm_client
from app.features.topics.schemas import TopicData, SeedData
from app.core.logging import get_logger
from app.core.errors import ValidationError

logger = get_logger(__name__)


RESEARCH_AGENT_SYSTEM_PROMPT = """You are a research agent for a UGC video production system.
Your task is to generate engaging topics for short-form vertical videos (TikTok/Instagram).

Each topic must have:
1. title: A compelling, attention-grabbing title (max 200 chars)
2. rotation: The hook/opening text that will be spoken (max 500 chars)
3. cta: A clear call-to-action (max 200 chars)
4. spoken_duration: Estimated duration in seconds for the rotation text (must be ≤8 seconds)

Topics should be:
- Engaging and shareable
- Suitable for short-form video (8 seconds or less)
- Relevant to the brand context
- Unique and creative

Output ONLY valid JSON with an array of topics."""


def generate_topics_research_agent(
    brand: str,
    post_type: str,
    count: int = 10
) -> List[TopicData]:
    """
    PROMPT 1: Research Agent
    Generate topics based on brand and post type.
    Per Canon § 6.1: Research Agent
    """
    llm = get_llm_client()
    
    prompt = f"""Generate {count} unique and engaging topics for {post_type} posts for the brand "{brand}".

Post type context:
- value: Educational, informative content that provides value
- lifestyle: Aspirational, lifestyle-focused content
- product: Product-focused, promotional content

Return a JSON object with a "topics" array. Each topic must have:
- title: string (max 200 chars)
- rotation: string (max 500 chars, spoken text)
- cta: string (max 200 chars)
- spoken_duration: number (seconds, max 8)

Example format:
{{
  "topics": [
    {{
      "title": "5 Morning Habits That Changed My Life",
      "rotation": "Want to transform your mornings? These 5 simple habits took me from chaos to clarity in just 30 days.",
      "cta": "Try habit #3 tomorrow!",
      "spoken_duration": 7.5
    }}
  ]
}}

Generate {count} diverse, engaging topics now:"""
    
    try:
        response = llm.generate_json(
            prompt=prompt,
            system_prompt=RESEARCH_AGENT_SYSTEM_PROMPT,
            provider="openai",
            model="gpt-4"
        )
        
        # Validate response structure
        if "topics" not in response:
            raise ValidationError(
                message="Invalid response structure from research agent",
                details={"response": response}
            )
        
        topics = []
        for topic_data in response["topics"]:
            try:
                topic = TopicData(**topic_data)
                topics.append(topic)
            except Exception as e:
                logger.warning(
                    "topic_validation_failed",
                    error=str(e),
                    topic_data=topic_data
                )
                continue
        
        logger.info(
            "research_agent_success",
            brand=brand,
            post_type=post_type,
            requested_count=count,
            generated_count=len(topics)
        )
        
        return topics
    
    except Exception as e:
        logger.error(
            "research_agent_failed",
            brand=brand,
            post_type=post_type,
            error=str(e)
        )
        raise


STRICT_EXTRACTOR_SYSTEM_PROMPT = """You are a strict fact extractor for a UGC video system.
Your ONLY job is to extract factual information from the provided topic.

Rules:
1. Extract ONLY facts that are explicitly stated or clearly implied
2. DO NOT add creative interpretations or embellishments
3. DO NOT hallucinate information
4. Keep facts concise and clear
5. If no clear facts are present, extract the core message/claim

Output ONLY valid JSON with a "facts" array of strings."""


def extract_seed_strict_extractor(topic: TopicData) -> SeedData:
    """
    Strict Extractor Agent
    Extract factual seed data from topic (no hallucination).
    Per Canon § 6.2: Strict Extractor Agent
    """
    llm = get_llm_client()
    
    prompt = f"""Extract factual seed information from this topic:

Title: {topic.title}
Rotation: {topic.rotation}
CTA: {topic.cta}

Extract ONLY the factual claims, core messages, or key points. Do not add any creative interpretation.

Return JSON format:
{{
  "facts": ["fact 1", "fact 2", ...],
  "source_context": "brief context if needed"
}}

Extract facts now:"""
    
    try:
        response = llm.generate_json(
            prompt=prompt,
            system_prompt=STRICT_EXTRACTOR_SYSTEM_PROMPT,
            provider="openai",
            model="gpt-4"
        )
        
        seed = SeedData(**response)
        
        logger.info(
            "strict_extractor_success",
            topic_title=topic.title[:50],
            facts_count=len(seed.facts)
        )
        
        return seed
    
    except Exception as e:
        logger.error(
            "strict_extractor_failed",
            topic_title=topic.title[:50],
            error=str(e)
        )
        raise
