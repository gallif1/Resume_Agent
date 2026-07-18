"""Dynamic profile extraction utilities for industry-agnostic job matching.

This module provides utilities to extract Target Role and Skills Matrix dynamically
from candidate profiles, supporting career-switchers and multi-industry candidates.
"""

from __future__ import annotations

from typing import Any


def extract_target_roles(profile: dict[str, Any], cv_profile: dict[str, Any]) -> list[str]:
    """Extract the candidate's target roles from profile and CV data.
    
    This is the "North Star" for matching - what the candidate wants to do,
    not necessarily what they've done before.
    
    Args:
        profile: User profile data (profile.json)
        cv_profile: Parsed CV data
        
    Returns:
        List of target role titles, prioritized
    """
    target_roles: list[str] = []
    seen: set[str] = set()
    
    # Priority 1: Explicit target roles from profile
    for role in profile.get("target_roles", []):
        role_str = str(role).strip()
        role_key = role_str.lower()
        if role_str and role_key not in seen:
            target_roles.append(role_str)
            seen.add(role_key)
    
    # Priority 2: Universal profile preferred roles
    universal = cv_profile.get("universal_profile", {})
    if isinstance(universal, dict):
        for role in universal.get("preferred_role_titles", []):
            role_str = str(role).strip()
            role_key = role_str.lower()
            if role_str and role_key not in seen:
                target_roles.append(role_str)
                seen.add(role_key)
    
    # Priority 3: AI-recommended roles from CV analysis
    if isinstance(cv_profile.get("best_fit_roles"), list):
        for role in cv_profile.get("best_fit_roles", []):
            role_str = str(role).strip()
            role_key = role_str.lower()
            if role_str and role_key not in seen:
                target_roles.append(role_str)
                seen.add(role_key)
    
    # Priority 4: AI insights recommended job types
    insights = cv_profile.get("ai_insights", {})
    if isinstance(insights, dict):
        for role in insights.get("recommended_job_types", []):
            role_str = str(role).strip()
            role_key = role_str.lower()
            if role_str and role_key not in seen:
                target_roles.append(role_str)
                seen.add(role_key)
    
    return target_roles


def extract_skills_matrix(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, list[str]]:
    """Extract the candidate's skills matrix dynamically.
    
    Returns a structured skills dictionary with categories.
    
    Args:
        profile: User profile data
        cv_profile: Parsed CV data
        
    Returns:
        Dictionary with skill categories as keys and skill lists as values
    """
    skills_matrix: dict[str, list[str]] = {
        "core_skills": [],
        "technologies": [],
        "domain_knowledge": [],
        "soft_skills": [],
    }
    
    # Extract from CV profile
    cv_skills = cv_profile.get("skills", {})
    if isinstance(cv_skills, dict):
        for category, skill_list in cv_skills.items():
            if not isinstance(skill_list, list):
                continue
            
            category_key = str(category).lower()
            
            # Map CV categories to our standardized matrix
            if any(term in category_key for term in ["technical", "hard", "core", "programming", "professional"]):
                skills_matrix["core_skills"].extend(str(s).strip() for s in skill_list if s)
            elif any(term in category_key for term in ["technology", "tool", "framework", "platform", "software"]):
                skills_matrix["technologies"].extend(str(s).strip() for s in skill_list if s)
            elif any(term in category_key for term in ["domain", "industry", "business", "knowledge"]):
                skills_matrix["domain_knowledge"].extend(str(s).strip() for s in skill_list if s)
            elif any(term in category_key for term in ["soft", "interpersonal", "communication", "leadership"]):
                skills_matrix["soft_skills"].extend(str(s).strip() for s in skill_list if s)
            else:
                # Default to core skills
                skills_matrix["core_skills"].extend(str(s).strip() for s in skill_list if s)
    elif isinstance(cv_skills, list):
        # If skills is a flat list, put everything in core_skills
        skills_matrix["core_skills"].extend(str(s).strip() for s in cv_skills if s)
    
    # Extract from universal profile
    universal = cv_profile.get("universal_profile", {})
    if isinstance(universal, dict):
        for skill in universal.get("canonical_skills", []):
            skill_str = str(skill).strip()
            if skill_str and skill_str not in skills_matrix["core_skills"]:
                skills_matrix["core_skills"].append(skill_str)
        
        for tech in universal.get("technologies_tools", []):
            tech_str = str(tech).strip()
            if tech_str and tech_str not in skills_matrix["technologies"]:
                skills_matrix["technologies"].append(tech_str)
        
        for domain in universal.get("domain_keywords", []):
            domain_str = str(domain).strip()
            if domain_str and domain_str not in skills_matrix["domain_knowledge"]:
                skills_matrix["domain_knowledge"].append(domain_str)
    
    # Extract from strengths
    for strength in cv_profile.get("strengths", []):
        strength_str = str(strength).strip()
        if strength_str and strength_str not in skills_matrix["core_skills"]:
            skills_matrix["core_skills"].append(strength_str)
    
    # Deduplicate and limit
    for category in skills_matrix:
        skills_matrix[category] = list(dict.fromkeys(skills_matrix[category]))[:50]
    
    return skills_matrix


def extract_projects(cv_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and structure project information for experience translation.
    
    Projects are critical for career-switchers and junior candidates as they
    demonstrate practical capability even without formal employment history.
    
    Args:
        cv_profile: Parsed CV data
        
    Returns:
        List of project dictionaries with structured information
    """
    projects: list[dict[str, Any]] = []
    
    raw_projects = cv_profile.get("projects", [])
    if not isinstance(raw_projects, list):
        return projects
    
    for project in raw_projects:
        if isinstance(project, dict):
            projects.append({
                "title": str(project.get("title", project.get("name", ""))).strip(),
                "description": str(project.get("description", "")).strip(),
                "technologies": project.get("technologies", []),
                "url": str(project.get("url", project.get("link", ""))).strip(),
            })
        elif isinstance(project, str):
            # If project is just a string, create a basic structure
            project_str = str(project).strip()
            if project_str:
                projects.append({
                    "title": "",
                    "description": project_str,
                    "technologies": [],
                    "url": "",
                })
    
    return projects


def extract_seniority_level(profile: dict[str, Any], cv_profile: dict[str, Any]) -> str:
    """Extract the candidate's seniority level.
    
    Args:
        profile: User profile data
        cv_profile: Parsed CV data
        
    Returns:
        Seniority level string (e.g., "junior", "mid", "senior", "unknown")
    """
    # Check CV experience
    experience = cv_profile.get("experience", {})
    if isinstance(experience, dict):
        seniority = experience.get("seniority_level")
        if seniority:
            return str(seniority).lower().strip()
    
    # Check universal profile
    universal = cv_profile.get("universal_profile", {})
    if isinstance(universal, dict):
        seniority = universal.get("seniority_level")
        if seniority:
            return str(seniority).lower().strip()
    
    # Default based on years of experience
    if isinstance(experience, dict):
        years = experience.get("years_of_experience_estimate")
        if years is not None:
            try:
                years_num = float(years)
                if years_num < 2:
                    return "junior"
                elif years_num < 5:
                    return "mid"
                elif years_num >= 5:
                    return "senior"
            except (TypeError, ValueError):
                pass
    
    return "unknown"


def build_dynamic_candidate_context(profile: dict[str, Any], cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Build a complete dynamic context for the candidate for matching.
    
    This context is used by the matching agent to understand the candidate's
    goals and capabilities without any hardcoded assumptions.
    
    Args:
        profile: User profile data
        cv_profile: Parsed CV data
        
    Returns:
        Structured context dictionary
    """
    target_roles = extract_target_roles(profile, cv_profile)
    skills_matrix = extract_skills_matrix(profile, cv_profile)
    projects = extract_projects(cv_profile)
    seniority = extract_seniority_level(profile, cv_profile)
    
    # Extract education for academic background
    education = cv_profile.get("education", {})
    if isinstance(education, dict):
        degrees = education.get("degrees", [])
        fields = education.get("fields_of_study", [])
    else:
        degrees = []
        fields = []
    
    # Extract employment history (but note: this should NOT bias matching)
    experience = cv_profile.get("experience", {})
    if isinstance(experience, dict):
        job_titles = experience.get("job_titles", [])
        years_experience = experience.get("years_of_experience_estimate", 0)
    else:
        job_titles = []
        years_experience = 0
    
    return {
        "target_roles": target_roles,
        "primary_target_role": target_roles[0] if target_roles else "Unknown",
        "skills_matrix": skills_matrix,
        "all_skills": (
            skills_matrix["core_skills"] + 
            skills_matrix["technologies"] + 
            skills_matrix["domain_knowledge"]
        ),
        "projects": projects,
        "project_count": len(projects),
        "seniority_level": seniority,
        "academic_background": {
            "degrees": degrees,
            "fields_of_study": fields,
        },
        "employment_history": {
            "titles": job_titles,
            "years_experience": years_experience,
        },
        "is_career_switcher": (
            len(target_roles) > 0 and 
            len(job_titles) > 0 and 
            not any(
                str(target).lower() in str(title).lower() or str(title).lower() in str(target).lower()
                for target in target_roles[:1]
                for title in job_titles[:2]
            )
        ),
    }
