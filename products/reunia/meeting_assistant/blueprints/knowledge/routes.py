from __future__ import annotations

from io import BytesIO

from flask import current_app, g, jsonify, redirect, render_template, request, send_file, session

from meeting_assistant.blueprints.knowledge import knowledge_bp
from meeting_assistant.services.knowledge_search_service import KnowledgeSearchService
from meeting_assistant.services.knowledge_service import KnowledgeService
from meeting_assistant.services.meeting_materials_service import MeetingMaterialsService
from meeting_assistant.services.admin_analytics_service import UsageMetricsService
from meeting_assistant.services.user_service import UserService
from meeting_assistant.utils.authentication import api_auth_required, login_required


@knowledge_bp.get("/interview-preparation")
@login_required
def open_interview_preparation():
    return redirect("/applications/interview-preparation", code=302)


@knowledge_bp.get("/career-profile")
@knowledge_bp.get("/application-materials")
@knowledge_bp.get("/career-evidence-library")
@knowledge_bp.get("/knowledge.html")
@login_required
def view_knowledge():
    if request.path == "/knowledge.html":
        return redirect("/career-evidence-library", code=302)

    context = UserService().get_assistant_context(session["user_id"])
    library = KnowledgeService().list_library(str(session["user_id"]))
    route_view = {
        "/career-profile": "context",
        "/application-materials": "materials",
        "/career-evidence-library": "library",
    }.get(request.path, "library")
    return render_template(
        "knowledge.html",
        route_view=route_view,
        files=library["files"],
        collections=library["collections"],
        assistant_context_storage_scope=session["user_id"],
        assistant_context_enabled=context["enabled"],
        assistant_context_professional_headline=context["professional_headline"],
        assistant_context_current_role=context["current_role"],
        assistant_context_years_experience=context["years_experience"],
        assistant_context_current_location=context["current_location"],
        assistant_context_preferred_roles=context["preferred_roles"],
        assistant_context_industries=context["industries"],
        assistant_context_core_skills=context["core_skills"],
        assistant_context_key_accomplishments=context["key_accomplishments"],
        assistant_context_countries_worked=context["countries_worked"],
        assistant_context_languages=context["languages"],
        assistant_context_target_country=context["target_country"],
        assistant_context_target_country_experience=context["target_country_experience"],
        assistant_context_international_credentials=context["international_credentials"],
        assistant_context_certifications=context["certifications"],
        assistant_context_titles_needing_translation=context["titles_needing_translation"],
        assistant_context_career_transition=context["career_transition"],
        assistant_context_work_preferences=context["work_preferences"],
        assistant_context_relocation_preferences=context["relocation_preferences"],
        assistant_context_work_authorization=context["work_authorization"],
        assistant_context_career_goals=context["career_goals"],
        assistant_context_constraints=context["constraints"],
        assistant_context_company=context["company"],
        assistant_context_reference_link=context["reference_link"],
        assistant_context_role=context["role"],
        assistant_context_type=context["type"],
        assistant_context_domain=context["domain"],
        assistant_context_objective=context["objective"],
        assistant_context_free_text=context["free_text"],
    )


@knowledge_bp.post("/api/career/evidence/search")
@knowledge_bp.post("/api/knowledge/ask")
@api_auth_required
def ask_knowledge():
    result = KnowledgeSearchService().answer(
        g.current_user_id,
        request.get_json(silent=True) or {},
    )
    return jsonify(result)


@knowledge_bp.get("/api/career/evidence")
@knowledge_bp.get("/api/knowledge/files")
@api_auth_required
def list_knowledge_files():
    files = KnowledgeService().list_files(g.current_user_id)
    return jsonify({"evidence_items": files, "files": files})


@knowledge_bp.post("/api/career/evidence")
@knowledge_bp.post("/api/knowledge/files")
@api_auth_required
def upload_knowledge_files():
    try:
        files = KnowledgeService().upload_files(
            g.current_user_id,
            request.files.getlist("files"),
            collection_id=request.form.get("collection_id", ""),
            tags=request.form.get("tags", ""),
            description=request.form.get("description", ""),
        )
        for item in files:
            UsageMetricsService().record_product_event(
                "document_uploaded", g.current_user_id,
                event_id=str(item.get("file_id") or item.get("item_id") or ""),
                metadata={"extension": item.get("extension", ""), "size_bytes": item.get("size_bytes", 0)},
            )
            UsageMetricsService().record_product_event(
                "document_processing_succeeded", g.current_user_id,
                event_id=str(item.get("file_id") or item.get("item_id") or ""),
                metadata={"extension": item.get("extension", "")},
            )
        return jsonify({"success": True, "files": files}), 201
    except Exception:
        try:
            UsageMetricsService().record_product_event("document_processing_failed", g.current_user_id)
        except Exception:
            current_app.logger.exception("Could not record document failure analytics")
        raise


@knowledge_bp.delete("/api/career/evidence/<file_id>")
@knowledge_bp.delete("/api/knowledge/files/<file_id>")
@api_auth_required
def delete_knowledge_file(file_id: str):
    deleted = KnowledgeService().delete_file(g.current_user_id, file_id)
    return jsonify({"success": True, "file": deleted})


@knowledge_bp.get("/api/career/evidence/<file_id>/download")
@knowledge_bp.get("/api/knowledge/files/<file_id>/download")
@api_auth_required
def download_knowledge_file(file_id: str):
    item, content = KnowledgeService().get_file(g.current_user_id, file_id)
    response = send_file(
        BytesIO(content),
        mimetype=str(item.get("content_type") or "application/octet-stream"),
        as_attachment=True,
        download_name=str(item.get("display_name") or item.get("filename") or "document"),
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@knowledge_bp.get("/api/career/evidence/<file_id>/preview")
@knowledge_bp.get("/api/knowledge/files/<file_id>/preview")
@api_auth_required
def preview_knowledge_file(file_id: str):
    item, content = KnowledgeService().get_file(g.current_user_id, file_id)
    response = send_file(
        BytesIO(content),
        mimetype=str(item.get("content_type") or "application/octet-stream"),
        as_attachment=False,
        download_name=str(item.get("display_name") or item.get("filename") or "document"),
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@knowledge_bp.get("/api/knowledge/collections")
@api_auth_required
def list_knowledge_collections():
    collections = KnowledgeService().list_collections(g.current_user_id)
    return jsonify({"collections": collections})


@knowledge_bp.post("/api/knowledge/collections")
@api_auth_required
def create_knowledge_collection():
    collection = KnowledgeService().create_collection(
        g.current_user_id,
        request.get_json(silent=True) or {},
    )
    return jsonify({"success": True, "collection": collection}), 201


@knowledge_bp.delete("/api/knowledge/collections/<collection_id>")
@api_auth_required
def delete_knowledge_collection(collection_id: str):
    deleted = KnowledgeService().delete_collection(g.current_user_id, collection_id)
    return jsonify({"success": True, "collection": deleted})


@knowledge_bp.get("/api/career/profile")
@knowledge_bp.get("/api/knowledge/context")
@login_required
def get_assistant_context():
    context = UserService().get_assistant_context(session["user_id"])
    return jsonify({"career_profile": context, "context": context})


@knowledge_bp.put("/api/career/profile")
@knowledge_bp.put("/api/knowledge/context")
@login_required
def update_assistant_context():
    data = request.get_json(silent=True) or {}
    context = UserService().update_assistant_context(session["user_id"], data)
    return jsonify(
        {
            "success": True,
            "career_profile": context,
            "context": context,
        }
    )


@knowledge_bp.get("/api/career/upcoming-interviews")
@knowledge_bp.get("/api/career/application-workspaces")
@knowledge_bp.get("/api/knowledge/upcoming-meetings")
@api_auth_required
def list_upcoming_meetings():
    service = MeetingMaterialsService()
    workspaces = service.list_meetings(g.current_user_id)
    active_workspace_id = service.get_active_meeting_id(g.current_user_id)
    return jsonify(
        {
            # Career Bridge names are preferred by new clients.
            "application_workspaces": workspaces,
            "upcoming_interviews": workspaces,
            "active_application_workspace_id": active_workspace_id,
            # Legacy aliases keep the imported Réunia UI and stored clients working.
            "meetings": workspaces,
            "active_meeting_id": active_workspace_id,
        }
    )


@knowledge_bp.post("/api/career/upcoming-interviews")
@knowledge_bp.post("/api/career/application-workspaces")
@knowledge_bp.post("/api/knowledge/upcoming-meetings")
@api_auth_required
def create_upcoming_meeting():
    workspace = MeetingMaterialsService().create_meeting(
        g.current_user_id,
        request.get_json(silent=True) or {},
    )
    return jsonify(
        {
            "success": True,
            "application_workspace": workspace,
            "upcoming_interview": workspace,
            "meeting": workspace,
        }
    ), 201


@knowledge_bp.put("/api/career/upcoming-interviews/<meeting_id>")
@knowledge_bp.put("/api/career/application-workspaces/<meeting_id>")
@knowledge_bp.put("/api/knowledge/upcoming-meetings/<meeting_id>")
@api_auth_required
def update_upcoming_meeting(meeting_id: str):
    workspace = MeetingMaterialsService().update_meeting(
        g.current_user_id,
        meeting_id,
        request.get_json(silent=True) or {},
    )
    return jsonify(
        {
            "success": True,
            "application_workspace": workspace,
            "upcoming_interview": workspace,
            "meeting": workspace,
        }
    )


@knowledge_bp.delete("/api/career/upcoming-interviews/<meeting_id>")
@knowledge_bp.delete("/api/career/application-workspaces/<meeting_id>")
@knowledge_bp.delete("/api/knowledge/upcoming-meetings/<meeting_id>")
@api_auth_required
def delete_upcoming_meeting(meeting_id: str):
    workspace = MeetingMaterialsService().delete_meeting(
        g.current_user_id,
        meeting_id,
    )
    return jsonify(
        {
            "success": True,
            "application_workspace": workspace,
            "upcoming_interview": workspace,
            "meeting": workspace,
        }
    )


@knowledge_bp.get("/api/career/application-materials")
@knowledge_bp.get("/api/knowledge/meeting-materials")
@api_auth_required
def get_meeting_materials():
    materials = MeetingMaterialsService().get_materials(
        g.current_user_id,
        request.args.get("application_workspace_id")
        or request.args.get("meeting_id", ""),
    )
    return jsonify({"application_materials": materials, "materials": materials})


@knowledge_bp.put("/api/career/application-materials")
@knowledge_bp.put("/api/knowledge/meeting-materials")
@api_auth_required
def save_meeting_materials():
    payload = request.get_json(silent=True) or {}
    if payload.get("application_workspace_id") and not payload.get("meeting_id"):
        payload["meeting_id"] = payload["application_workspace_id"]
    materials = MeetingMaterialsService().save_materials(
        g.current_user_id,
        payload,
    )
    return jsonify(
        {"success": True, "application_materials": materials, "materials": materials}
    )


@knowledge_bp.put("/api/career/upcoming-interviews/<meeting_id>/context")
@knowledge_bp.put("/api/career/application-workspaces/<meeting_id>/context")
@knowledge_bp.put("/api/knowledge/upcoming-meetings/<meeting_id>/context")
@api_auth_required
def save_upcoming_meeting_context(meeting_id: str):
    context = MeetingMaterialsService().save_meeting_context(
        g.current_user_id,
        meeting_id,
        request.get_json(silent=True) or {},
    )
    return jsonify({"success": True, "application_context": context, "context": context})


@knowledge_bp.post("/api/career/application-materials/<meeting_id>/temporary-files")
@knowledge_bp.post("/api/knowledge/meeting-materials/<meeting_id>/temporary-files")
@api_auth_required
def upload_meeting_temporary_files(meeting_id: str):
    files = MeetingMaterialsService().upload_temporary_files(
        g.current_user_id,
        meeting_id,
        request.files.getlist("files"),
    )
    return jsonify({"success": True, "files": files}), 201


@knowledge_bp.delete("/api/career/application-materials/<meeting_id>/temporary-files/<file_id>")
@knowledge_bp.delete("/api/knowledge/meeting-materials/<meeting_id>/temporary-files/<file_id>")
@api_auth_required
def delete_meeting_temporary_file(meeting_id: str, file_id: str):
    MeetingMaterialsService().delete_temporary_file(g.current_user_id, meeting_id, file_id)
    return jsonify({"success": True})


@knowledge_bp.delete("/api/career/application-materials/<meeting_id>/temporary-files")
@knowledge_bp.delete("/api/knowledge/meeting-materials/<meeting_id>/temporary-files")
@api_auth_required
def clear_meeting_temporary_files(meeting_id: str):
    MeetingMaterialsService().clear_temporary_files(g.current_user_id, meeting_id)
    return jsonify({"success": True})


@knowledge_bp.put("/api/career/active-application")
@knowledge_bp.put("/api/knowledge/active-meeting")
@api_auth_required
def set_active_meeting():
    payload = request.get_json(silent=True) or {}
    meeting_id = MeetingMaterialsService().set_active_meeting(
        g.current_user_id,
        str(
            payload.get("application_workspace_id")
            or payload.get("meeting_id")
            or ""
        ),
    )
    return jsonify(
        {
            "success": True,
            "active_application_workspace_id": meeting_id,
            "active_meeting_id": meeting_id,
        }
    )
