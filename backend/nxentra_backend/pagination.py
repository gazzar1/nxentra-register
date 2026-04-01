# nxentra_backend/pagination.py
"""
Reusable pagination + sorting utility for APIView-based list endpoints.

Usage in any view:

    from nxentra_backend.pagination import paginate_queryset

    def get(self, request):
        qs = MyModel.objects.filter(company=actor.company)
        return paginate_queryset(
            request, qs, MySerializer,
            default_ordering="-date",
            allowed_sort_fields=["date", "amount", "code"],
        )

Response format:
    {
        "results": [...],
        "count": 120,
        "page": 1,
        "page_size": 25,
        "total_pages": 5
    }

Query params:
    ?page=2&page_size=50&ordering=-date&search=term
"""

import math

from rest_framework.response import Response

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200


def paginate_queryset(
    request,
    queryset,
    serializer_class,
    *,
    default_ordering="-id",
    allowed_sort_fields=None,
    serializer_context=None,
):
    """
    Paginate and optionally sort a queryset, returning a paginated Response.

    Args:
        request: DRF Request object (reads page, page_size, ordering query params)
        queryset: Django QuerySet to paginate
        serializer_class: DRF Serializer for the results
        default_ordering: Default ordering if none specified
        allowed_sort_fields: List of field names the client can sort by.
                             Prefix with "-" for descending is handled automatically.
        serializer_context: Extra context to pass to the serializer

    Returns:
        Response with paginated data
    """
    # ── Sorting ──────────────────────────────────────────────
    ordering = request.query_params.get("ordering", default_ordering)
    if allowed_sort_fields and ordering:
        # Strip leading "-" to check the base field name
        base_field = ordering.lstrip("-")
        if base_field not in allowed_sort_fields:
            ordering = default_ordering
    queryset = queryset.order_by(ordering)

    # ── Pagination ───────────────────────────────────────────
    total_count = queryset.count()

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        page_size = min(
            MAX_PAGE_SIZE,
            max(1, int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))),
        )
    except (ValueError, TypeError):
        page_size = DEFAULT_PAGE_SIZE

    total_pages = max(1, math.ceil(total_count / page_size))
    # Clamp page to valid range
    page = min(page, total_pages)

    offset = (page - 1) * page_size
    page_qs = queryset[offset : offset + page_size]

    # ── Serialize ────────────────────────────────────────────
    ctx = {"request": request}
    if serializer_context:
        ctx.update(serializer_context)

    serializer = serializer_class(page_qs, many=True, context=ctx)

    return Response({
        "results": serializer.data,
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })
