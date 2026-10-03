"""Response envelope renderer (spec 43)."""

from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    """Wrap responses as {success, message, data} or {success, message, errors}.

    Pagination nests *inside* `data` rather than being flattened into the
    envelope:

        {"success": true, "message": "...", "data": {
            "count": 240, "next": ..., "previous": ..., "page": 1,
            "total_pages": 12, "results": [...]}}

    So a paginated hook reads `data.results` and a plain hook reads `data` --
    one rule, no special cases.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}
        response = renderer_context.get("response")

        # Pass through anything already enveloped (exception handler output, or
        # a view that built its own envelope).
        if isinstance(data, dict) and "success" in data and ("data" in data or "errors" in data):
            return super().render(data, accepted_media_type, renderer_context)

        status_code = getattr(response, "status_code", 200)
        success = 200 <= status_code < 400
        message = getattr(response, "envelope_message", "") or ("" if success else "Request failed.")

        if success:
            payload = {"success": True, "message": message, "data": data}
        else:
            payload = {"success": False, "message": message, "errors": data}

        return super().render(payload, accepted_media_type, renderer_context)
