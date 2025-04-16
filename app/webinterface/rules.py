"""
rules.py
========
Rules page for the graphical user interface of mercure.
"""

import json
# Standard python includes
from typing import Any, Dict, Set, List, Optional

# App-specific includes
import common.config as config
import common.monitor as monitor
import common.rule_evaluation as rule_evaluation
import common.tagslist as tagslist
from common.tags_rule_interface import TagNotFoundException
from common.types import Rule
from decoRouter import Router as decoRouter
# Starlette-related includes
from starlette.applications import Starlette
from starlette.authentication import requires
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from webinterface.common import templates

from pydantic import BaseModel, EmailStr, constr, ValidationError
import bleach

router = decoRouter()

logger = config.get_logger()

##########################
# Helper: HTML Sanitization
##########################
def sanitize_html(html_content: str) -> str:
    """
    Clean the HTML content using bleach to allow only certain safe tags.
    """
    allowed_tags = ['b', 'i', 'em', 'strong', 'p', 'br', 'ul', 'ol', 'li']
    allowed_attributes = {
        'a': ['href', 'title']
    }
    return bleach.clean(html_content, tags=allowed_tags, attributes=allowed_attributes, strip=True)

def sanitize_field(field_value: str) -> str:
    """
    Sanitize a plain text field by stripping out all HTML tags.
    Use this for all fields that expect plain text.
    """
    return bleach.clean(field_value, tags=[], strip=True)

##########################
# Helper: Pydantic model for rule update
##########################
class RuleUpdateInput(BaseModel):
    # Basic rule fields; adjust constraints as needed.
    rule: constr(min_length=1)
    target: List[str]
    status_disabled: bool = False
    status_fallback: bool = False
    contact: Optional[EmailStr] = None
    comment: Optional[str] = ""
    tags: Optional[str] = ""
    action: Optional[str] = "route"
    action_trigger: Optional[str] = "series"
    study_trigger_condition: Optional[str] = "timeout"
    study_trigger_series: Optional[str] = ""
    study_force_completion_action: Optional[str] = ""
    priority: Optional[str] = "normal"
    # In some cases processing_module may be a comma-separated string
    processing_module: Optional[str] = ""
    processing_settings: Optional[Dict] = {}
    processing_retain_images: bool = False
    notification_webhook: Optional[str] = ""
    notification_email: Optional[str] = ""
    # Fields that might contain HTML:
    notification_payload: Optional[str] = ""
    notification_payload_body: Optional[str] = ""
    notification_email_body: Optional[str] = ""
    notification_email_html: bool = False
    notification_trigger_reception: bool = False
    notification_trigger_completion: bool = False
    notification_trigger_completion_on_request: bool = False
    notification_trigger_error: bool = False


###################################################################################
# Rules endpoints
###################################################################################

@router.get("/")
@requires("authenticated", redirect="login")
async def rules(request) -> Response:
    """Show all defined routing rules. Can be executed by all logged-in users."""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")

    template = "rules.html"
    context = {
        "request": request,
        "page": "rules",
        "rules": config.mercure.rules,
    }
    return templates.TemplateResponse(template, context)


@router.post("/duplicate")
@requires(["authenticated", "admin"], redirect="login")
async def duplicate_rule(request) -> Response:
    """Duplicates an existing routing rule."""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")
    form = await request.form()
    new_name = sanitize_field(form.get("new_name", "").strip())
    old_name = sanitize_field(form.get("old_name", "").strip())
    if not old_name or not new_name or old_name == new_name or new_name in config.mercure.rules:
        return PlainTextResponse("Invalid input or duplicate name.")

    config.mercure.rules[new_name] = Rule(**config.mercure.rules[old_name].__dict__)

    # return RedirectResponse(url="/rules", status_code=303)
    return RedirectResponse(url="/rules/edit/" + new_name, status_code=303)


@router.post("/")
@requires(["authenticated", "admin"], redirect="login")
async def add_rule(request) -> Response:
    """Creates a new routing rule and forwards the user to the rule edit page."""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")

    form = dict(await request.form())

    newrule = sanitize_field(form.get("name", ""))
    if newrule in config.mercure.rules:
        return PlainTextResponse("Rule already exists.")

    default_payload_body = """Rule "{{ rule }}" triggered {{ event }}
{% if details is defined and details|length %}
Details:
{{ details }}
{% endif %}"""
    default_email_body = """Rule "{{ rule }}" triggered {{ event }}
Name: {{ patient_name }}
ACC: {{ acc }}
MRN: {{ mrn }}
{% if details is defined and details|length %}
Details:
{{ details }}
{% endif %}"""
    config.mercure.rules[newrule] = Rule(rule="False",
                                         notification_payload_body=default_payload_body,
                                         notification_email_body=default_email_body)

    try:
        config.save_config()
    except Exception:
        return PlainTextResponse("ERROR: Unable to write configuration. Try again.")

    logger.info(f"Created rule {newrule}")
    monitor.send_webgui_event(monitor.w_events.RULE_CREATE, request.user.display_name, newrule)
    return RedirectResponse(url="/rules/edit/" + newrule, status_code=303)


@router.get("/edit/{rule}")
@requires(["authenticated", "admin"], redirect="login")
async def rules_edit(request) -> Response:
    """Shows the edit page for the given routing rule."""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")

    rule = request.path_params["rule"]
    if rule not in config.mercure.rules:
        return PlainTextResponse("Rule does not exist anymore.")

    settings_string = ""
    if config.mercure.rules[rule].processing_settings:
        settings_string = json.dumps(config.mercure.rules[rule].processing_settings, indent=4, sort_keys=False)

    context = {
        "request": request,
        "page": "rules",
        "rules": config.mercure.rules,
        "targets": [t for t in config.mercure.targets if config.mercure.targets[t].direction in ("push", "both")],
        "modules": config.mercure.modules,
        "rule": rule,
        "alltags": tagslist.alltags,
        "sortedtags": tagslist.sortedtags,
        "processing_settings": settings_string,
        "process_runner": config.mercure.process_runner,
        "phi_notifications": config.mercure.phi_notifications,
    }

    template = "rules_edit.html"
    return templates.TemplateResponse(template, context)


@router.post("/edit/{rule}")
@requires(["authenticated", "admin"], redirect="login")
async def rules_edit_post(request) -> Response:
    """Updates the settings for the given routing rule."""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")

    editrule = sanitize_field(request.path_params["rule"])
    if editrule not in config.mercure.rules:
        return PlainTextResponse("Rule does not exist anymore.")

    try:
        form_data = await request.form()
        form = dict(form_data)
        target_list = [sanitize_field(t) for t in form_data.getlist("target")]
    except Exception:
        return PlainTextResponse("Invalid form data.")


    # Pre-process some fields
    # Attempt to load processing settings as JSON
    try:
        processing_settings = json.loads(form.get("processing_settings", "{}"))
    except Exception:
        processing_settings = {}

    # Handle processing_module field logic
    if "processing_module_list" in form:
        proc_mod_list = [sanitize_field(x) for x in form.get("processing_module_list", "").split(",") if x.strip()]
        processing_module = ",".join(proc_mod_list) if proc_mod_list else ""
    else:
        processing_module = sanitize_field(form.get("processing_module", ""))

    # Trim and normalize the notification payload
    notification_payload = sanitize_field(form.get("notification_payload", "").strip().lstrip("{").rstrip("}"))

    raw_contact = form.get("contact", "")
    sanitized_contact = sanitize_field(raw_contact).strip()

    # Build a dictionary of inputs expected by our Pydantic model
    # Note: The keys here match the model names. You might need to adjust based on your actual Rule type.
    input_data = {
        "rule": sanitize_field(form.get("rule", "False")),
        "target": target_list,
        "status_disabled": form.get("status_disabled", "False"),
        "status_fallback": form.get("status_fallback", "False"),
        "contact": sanitized_contact if sanitized_contact else None,
        "comment": sanitize_html(form.get("comment", "")),
        "tags": sanitize_field(form.get("tags", "")),
        "action": sanitize_field(form.get("action", "route")),
        "action_trigger": sanitize_field(form.get("action_trigger", "series")),
        "study_trigger_condition": sanitize_field(form.get("study_trigger_condition", "timeout")),
        "study_trigger_series": sanitize_field(form.get("study_trigger_series", "")),
        "study_force_completion_action": sanitize_field(form.get("study_force_completion_action", "")),
        "priority": sanitize_field(form.get("priority", "normal")),
        "processing_module": processing_module,
        "processing_settings": processing_settings,
        "processing_retain_images": form.get("processing_retain_images", "False"),
        "notification_webhook": sanitize_field(form.get("notification_webhook", "")),
        "notification_email": sanitize_field(form.get("notification_email", "")),
        "notification_payload": sanitize_html(notification_payload),
        "notification_payload_body": sanitize_html(form.get("notification_payload_body", "")),
        "notification_email_body": sanitize_html(form.get("notification_email_body", "")),
        "notification_email_html": form.get("notification_email_html", False),
        "notification_trigger_reception": form.get("notification_trigger_reception", "False"),
        "notification_trigger_completion": form.get("notification_trigger_completion", "False"),
        "notification_trigger_completion_on_request": form.get("notification_trigger_completion_on_request", "False"),
        "notification_trigger_error": form.get("notification_trigger_error", "False"),
    }

    # Validate and convert the input data using Pydantic.
    try:
        validated_data = RuleUpdateInput(**input_data)
    except ValidationError as ve:
        return PlainTextResponse(f"Input validation error: {ve}", status_code=400)

    # Sanitize HTML inputs
    validated_data.comment = sanitize_html(validated_data.comment)
    validated_data.notification_payload_body = sanitize_html(validated_data.notification_payload_body)
    validated_data.notification_email_body = sanitize_html(validated_data.notification_email_body)
    # Optionally, if notification_payload may contain HTML, sanitize it as well.
    validated_data.notification_payload = sanitize_html(validated_data.notification_payload)

    contact_for_rule = validated_data.contact if validated_data.contact is not None else ""
    
    try:
        new_rule: Rule = Rule(
            rule=validated_data.rule,
            target=validated_data.target,
            disabled=validated_data.status_disabled,  # Assuming Rule expects bool directly or string conversion happens elsewhere/is okay
            fallback=validated_data.status_fallback,  # Same assumption as disabled
            contact=contact_for_rule, # Use the converted "" or the original email
            comment=validated_data.comment,
            tags=validated_data.tags,
            action=validated_data.action,
            action_trigger=validated_data.action_trigger,
            study_trigger_condition=validated_data.study_trigger_condition,
            study_trigger_series=validated_data.study_trigger_series,
            study_force_completion_action=validated_data.study_force_completion_action,
            priority=validated_data.priority,
            processing_module=validated_data.processing_module,
            processing_settings=validated_data.processing_settings,
            processing_retain_images=validated_data.processing_retain_images, # Assuming Rule expects bool
            notification_webhook=validated_data.notification_webhook,
            notification_email=validated_data.notification_email, # Assuming "" is okay here if Rule expects str
            notification_payload=validated_data.notification_payload,
            notification_payload_body=validated_data.notification_payload_body,
            notification_email_body=validated_data.notification_email_body,
            notification_email_type="html" if validated_data.notification_email_html else "plain",
            notification_trigger_reception=validated_data.notification_trigger_reception, # Assuming Rule expects bool
            notification_trigger_completion=validated_data.notification_trigger_completion, # Assuming Rule expects bool
            notification_trigger_completion_on_request=validated_data.notification_trigger_completion_on_request, # Assuming Rule expects bool
            notification_trigger_error=validated_data.notification_trigger_error, # Assuming Rule expects bool
        )
    except ValidationError as ve_rule:
        # Handle potential validation errors from the Rule class itself if needed
        logger.error(f"Failed to create Rule object: {ve_rule}")
        return PlainTextResponse(f"Internal configuration error creating rule object: {ve_rule}", status_code=500)
    except Exception as e:
        # Catch other unexpected errors during Rule creation
        logger.error(f"Unexpected error creating Rule object: {e}", exc_info=True)
        return PlainTextResponse(f"Unexpected internal error creating rule object.", status_code=500)

    config.mercure.rules[editrule] = new_rule

    try:
        config.save_config()
    except Exception:
        return PlainTextResponse("ERROR: Unable to write configuration. Try again.")

    logger.info(f"Edited rule {editrule}")
    monitor.send_webgui_event(monitor.w_events.RULE_EDIT, request.user.display_name, editrule)
    return RedirectResponse(url="/rules", status_code=303)


@router.post("/delete/{rule}")
@requires(["authenticated", "admin"], redirect="login")
async def rules_delete_post(request) -> Response:
    """Deletes the given routing rule"""
    try:
        config.read_config()
    except Exception:
        return PlainTextResponse("Configuration is being updated. Try again in a minute.")

    deleterule = request.path_params["rule"]

    if deleterule in config.mercure.rules:
        del config.mercure.rules[deleterule]

    try:
        config.save_config()
    except Exception:
        return PlainTextResponse("ERROR: Unable to write configuration. Try again.")

    logger.info(f"Deleted rule {deleterule}")
    monitor.send_webgui_event(monitor.w_events.RULE_DELETE, request.user.display_name, deleterule)
    return RedirectResponse(url="/rules", status_code=303)


@router.post("/test")
@requires(["authenticated", "admin"], redirect="login")
async def rules_test(request) -> Response:
    """Evalutes if a given routing rule is valid. The rule and testing dictionary have to be passed as form parameters."""
    noresult: Set[Any] = set()
    attrs_accessed = set()
    try:
        form = dict(await request.form())
        testrule = sanitize_field(form["rule"])
        testvalues = json.loads(form["testvalues"])
    except Exception:
        return PlainTextResponse(
            ('<span class="tag is-warning is-medium ruleresult">'
             '<i class="fas fa-bug"></i>&nbsp;Error</span>&nbsp;&nbsp;Invalid test values')
        )
    try:
        result, attrs_accessed = rule_evaluation.eval_rule(testrule, testvalues)

        if result:
            style = "success"
            icon = "thumbs-up"
            text = "Trigger"
            inline = result if result is not True else noresult
        else:
            style = "info"
            icon = "thumbs-down"
            text = "Reject"
            inline = result if result is not False else noresult

    except TagNotFoundException as e:
        style = "info"
        icon = "thumbs-down"
        text = "Reject"
        inline = e

    except Exception as e:
        style = "danger"
        icon = "bug"
        text = "Error"
        inline = e

    # Sanitize attributes information before output.
    attrs_accessed_info = ("\n".join(
        [f"{sanitize_field(str(x))} = \"{sanitize_field(str(testvalues.get(x, '')))}\""
         for x in attrs_accessed])
                           if attrs_accessed else None)
    _inline = sanitize_field(repr(inline)) if not isinstance(inline, Exception) else sanitize_field(str(inline))
    return PlainTextResponse(
        f'<span class="tag is-{style} is-medium ruleresult">'
        f'<i class="fas fa-{icon}"></i>&nbsp;{text}</span>'
        + (f'<pre style="display:inline; margin-left: 1em">{_inline}</pre>' if inline is not noresult else '')
        + (f'<pre style="margin: 1em">Tags evaluated:\n{attrs_accessed_info}</pre>' if attrs_accessed_info else '')
    )


@router.post("/test_completionseries")
@requires(["authenticated", "admin"], redirect="login")
async def rules_test_completionseries(request) -> Response:
    """Evalutes if a given value for the series list for study completion is valid."""
    try:
        form = dict(await request.form())
        test_series_list = sanitize_field(form["study_trigger_series"])
    except Exception:
        return PlainTextResponse(
            '<span class="tag is-warning is-medium ruleresult"><i class="fas fa-bug"></i>&nbsp;Error</span>&nbsp;&nbsp;Invalid'
        )

    result = rule_evaluation.test_completion_series(test_series_list)

    if result == "True":
        return PlainTextResponse('<i class="fas fa-check-circle fa-lg has-text-success"></i>&nbsp;&nbsp;Valid')
    else:
        return PlainTextResponse(
            '<i class="fas fa-times-circle fa-lg has-text-danger"></i>&nbsp;&nbsp;Invalid: ' + result
        )

rules_app = Starlette(routes=router)
