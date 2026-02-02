# edim/views.py
"""
Thin views that delegate to the commands layer.

Views handle: HTTP parsing, authentication, response formatting.
Commands handle: business logic, validation, events.

CRITICAL: All mutations (create, update, delete) MUST go through commands
to ensure events are emitted. Views should never directly call .save() on models.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from accounts.authz import resolve_actor, require
from edim.models import (
    SourceSystem,
    MappingProfile,
    IdentityCrosswalk,
    IngestionBatch,
)
from edim.serializers import (
    # Source System serializers
    SourceSystemSerializer,
    SourceSystemCreateSerializer,
    SourceSystemUpdateSerializer,
    # Mapping Profile serializers
    MappingProfileSerializer,
    MappingProfileCreateSerializer,
    MappingProfileUpdateSerializer,
    # Crosswalk serializers
    IdentityCrosswalkSerializer,
    IdentityCrosswalkCreateSerializer,
    IdentityCrosswalkUpdateSerializer,
    CrosswalkRejectSerializer,
    # Batch serializers
    IngestionBatchSerializer,
    IngestionBatchDetailSerializer,
    StagedRecordSerializer,
    BatchUploadSerializer,
    BatchMapSerializer,
    BatchRejectSerializer,
)
from edim.commands import (
    # Source System commands
    create_source_system,
    update_source_system,
    deactivate_source_system,
    # Mapping Profile commands
    create_mapping_profile,
    update_mapping_profile,
    activate_mapping_profile,
    deprecate_mapping_profile,
    # Crosswalk commands
    create_crosswalk,
    update_crosswalk,
    verify_crosswalk,
    reject_crosswalk,
    # Batch commands
    stage_batch,
    map_batch,
    validate_batch,
    preview_batch,
    commit_batch,
    reject_batch,
)


# =============================================================================
# Source System Views
# =============================================================================

class SourceSystemListCreateView(APIView):
    """
    GET /api/edim/source-systems/ -> list source systems
    POST /api/edim/source-systems/ -> create source system
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        source_systems = SourceSystem.objects.filter(
            company=actor.company,
        ).order_by("name")
        serializer = SourceSystemSerializer(source_systems, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = SourceSystemCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = create_source_system(actor, **input_serializer.validated_data)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = SourceSystemSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class SourceSystemDetailView(APIView):
    """
    GET /api/edim/source-systems/<pk>/ -> retrieve source system
    PATCH /api/edim/source-systems/<pk>/ -> update source system
    DELETE /api/edim/source-systems/<pk>/ -> deactivate source system
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        try:
            return SourceSystem.objects.get(pk=pk, company=actor.company)
        except SourceSystem.DoesNotExist:
            from django.http import Http404
            raise Http404

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        source_system = self.get_object(actor, pk)
        serializer = SourceSystemSerializer(source_system)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        source_system = self.get_object(actor, pk)

        input_serializer = SourceSystemUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = update_source_system(
            actor, source_system.id, **input_serializer.validated_data
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = SourceSystemSerializer(result.data)
        return Response(output_serializer.data)

    def delete(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        source_system = self.get_object(actor, pk)

        result = deactivate_source_system(actor, source_system.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Mapping Profile Views
# =============================================================================

class MappingProfileListCreateView(APIView):
    """
    GET /api/edim/mapping-profiles/ -> list mapping profiles
    POST /api/edim/mapping-profiles/ -> create mapping profile
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        profiles = MappingProfile.objects.filter(
            company=actor.company,
        ).select_related("source_system", "created_by").order_by(
            "source_system__name", "document_type", "-version"
        )
        serializer = MappingProfileSerializer(profiles, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = MappingProfileCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = create_mapping_profile(actor, **input_serializer.validated_data)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MappingProfileSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class MappingProfileDetailView(APIView):
    """
    GET /api/edim/mapping-profiles/<pk>/ -> retrieve mapping profile
    PATCH /api/edim/mapping-profiles/<pk>/ -> update mapping profile
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        try:
            return MappingProfile.objects.select_related(
                "source_system", "created_by"
            ).get(pk=pk, company=actor.company)
        except MappingProfile.DoesNotExist:
            from django.http import Http404
            raise Http404

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        profile = self.get_object(actor, pk)
        serializer = MappingProfileSerializer(profile)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        profile = self.get_object(actor, pk)

        input_serializer = MappingProfileUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = update_mapping_profile(
            actor, profile.id, **input_serializer.validated_data
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MappingProfileSerializer(result.data)
        return Response(output_serializer.data)


class MappingProfileActivateView(APIView):
    """POST /api/edim/mapping-profiles/<pk>/activate/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            profile = MappingProfile.objects.get(pk=pk, company=actor.company)
        except MappingProfile.DoesNotExist:
            return Response(
                {"detail": "Mapping profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = activate_mapping_profile(actor, profile.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MappingProfileSerializer(result.data)
        return Response(output_serializer.data)


class MappingProfileDeprecateView(APIView):
    """POST /api/edim/mapping-profiles/<pk>/deprecate/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            profile = MappingProfile.objects.get(pk=pk, company=actor.company)
        except MappingProfile.DoesNotExist:
            return Response(
                {"detail": "Mapping profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = deprecate_mapping_profile(actor, profile.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = MappingProfileSerializer(result.data)
        return Response(output_serializer.data)


# =============================================================================
# Identity Crosswalk Views
# =============================================================================

class CrosswalkListCreateView(APIView):
    """
    GET /api/edim/crosswalks/ -> list crosswalks
    POST /api/edim/crosswalks/ -> create crosswalk
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        # Optional filters
        source_system_id = request.query_params.get("source_system")
        object_type = request.query_params.get("object_type")
        status_filter = request.query_params.get("status")

        crosswalks = IdentityCrosswalk.objects.filter(
            company=actor.company,
        ).select_related("source_system", "verified_by")

        if source_system_id:
            crosswalks = crosswalks.filter(source_system_id=source_system_id)
        if object_type:
            crosswalks = crosswalks.filter(object_type=object_type)
        if status_filter:
            crosswalks = crosswalks.filter(status=status_filter)

        crosswalks = crosswalks.order_by(
            "source_system__name", "object_type", "external_id"
        )
        serializer = IdentityCrosswalkSerializer(crosswalks, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = IdentityCrosswalkCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = create_crosswalk(actor, **input_serializer.validated_data)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IdentityCrosswalkSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class CrosswalkDetailView(APIView):
    """
    GET /api/edim/crosswalks/<pk>/ -> retrieve crosswalk
    PATCH /api/edim/crosswalks/<pk>/ -> update crosswalk
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        try:
            return IdentityCrosswalk.objects.select_related(
                "source_system", "verified_by"
            ).get(pk=pk, company=actor.company)
        except IdentityCrosswalk.DoesNotExist:
            from django.http import Http404
            raise Http404

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        crosswalk = self.get_object(actor, pk)
        serializer = IdentityCrosswalkSerializer(crosswalk)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        crosswalk = self.get_object(actor, pk)

        input_serializer = IdentityCrosswalkUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        result = update_crosswalk(
            actor, crosswalk.id, **input_serializer.validated_data
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IdentityCrosswalkSerializer(result.data)
        return Response(output_serializer.data)


class CrosswalkVerifyView(APIView):
    """POST /api/edim/crosswalks/<pk>/verify/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            crosswalk = IdentityCrosswalk.objects.get(pk=pk, company=actor.company)
        except IdentityCrosswalk.DoesNotExist:
            return Response(
                {"detail": "Crosswalk not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = verify_crosswalk(actor, crosswalk.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IdentityCrosswalkSerializer(result.data)
        return Response(output_serializer.data)


class CrosswalkRejectView(APIView):
    """POST /api/edim/crosswalks/<pk>/reject/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = CrosswalkRejectSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            crosswalk = IdentityCrosswalk.objects.get(pk=pk, company=actor.company)
        except IdentityCrosswalk.DoesNotExist:
            return Response(
                {"detail": "Crosswalk not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = reject_crosswalk(
            actor, crosswalk.id, reason=input_serializer.validated_data.get("reason", "")
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IdentityCrosswalkSerializer(result.data)
        return Response(output_serializer.data)


# =============================================================================
# Ingestion Batch Views
# =============================================================================

class BatchListView(APIView):
    """GET /api/edim/batches/ -> list ingestion batches"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        # Optional filters
        status_filter = request.query_params.get("status")
        source_system_id = request.query_params.get("source_system")

        batches = IngestionBatch.objects.filter(
            company=actor.company,
        ).select_related(
            "source_system", "mapping_profile", "staged_by", "committed_by", "rejected_by"
        )

        if status_filter:
            batches = batches.filter(status=status_filter)
        if source_system_id:
            batches = batches.filter(source_system_id=source_system_id)

        batches = batches.order_by("-created_at")
        serializer = IngestionBatchSerializer(batches, many=True)
        return Response(serializer.data)


class BatchUploadView(APIView):
    """POST /api/edim/batches/upload/ -> upload and stage a new batch"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = BatchUploadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        file = input_serializer.validated_data["file"]
        filename = file.name

        result = stage_batch(
            actor,
            source_system_id=input_serializer.validated_data["source_system_id"],
            file=file,
            filename=filename,
            mapping_profile_id=input_serializer.validated_data.get("mapping_profile_id"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class BatchDetailView(APIView):
    """GET /api/edim/batches/<pk>/ -> retrieve batch details"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        try:
            batch = IngestionBatch.objects.select_related(
                "source_system", "mapping_profile", "staged_by", "committed_by", "rejected_by"
            ).get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = IngestionBatchSerializer(batch)
        return Response(serializer.data)


class BatchRecordsView(APIView):
    """GET /api/edim/batches/<pk>/records/ -> list staged records in batch"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "edim.view")

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Pagination
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 100))
        offset = (page - 1) * page_size

        records = batch.records.all().order_by("row_number")[offset:offset + page_size]
        total = batch.records.count()

        serializer = StagedRecordSerializer(records, many=True)
        return Response({
            "page": page,
            "page_size": page_size,
            "total": total,
            "records": serializer.data,
        })


class BatchMapView(APIView):
    """POST /api/edim/batches/<pk>/map/ -> apply mapping to batch"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = BatchMapSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = map_batch(
            actor,
            batch.id,
            mapping_profile_id=input_serializer.validated_data.get("mapping_profile_id"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data)
        return Response(output_serializer.data)


class BatchValidateView(APIView):
    """POST /api/edim/batches/<pk>/validate/ -> validate batch"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = validate_batch(actor, batch.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data)
        return Response(output_serializer.data)


class BatchPreviewView(APIView):
    """POST /api/edim/batches/<pk>/preview/ -> preview batch journal entries"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = preview_batch(actor, batch.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data["batch"])
        return Response({
            "batch": output_serializer.data,
            "preview": result.data["preview"],
        })


class BatchCommitView(APIView):
    """POST /api/edim/batches/<pk>/commit/ -> commit batch to journal entries"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = commit_batch(actor, batch.id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data)
        return Response(output_serializer.data)


class BatchRejectView(APIView):
    """POST /api/edim/batches/<pk>/reject/ -> reject batch"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command

        input_serializer = BatchRejectSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            batch = IngestionBatch.objects.get(pk=pk, company=actor.company)
        except IngestionBatch.DoesNotExist:
            return Response(
                {"detail": "Batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = reject_batch(
            actor, batch.id, reason=input_serializer.validated_data.get("reason", "")
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output_serializer = IngestionBatchSerializer(result.data)
        return Response(output_serializer.data)
