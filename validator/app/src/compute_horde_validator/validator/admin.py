from django.contrib import admin  # noqa
from django.contrib import messages  # noqa
from django.utils.safestring import mark_safe  # noqa
from django import forms

from compute_horde_validator.validator.models import (
    Miner,
    OrganicJob,
    SyntheticJob,
    MinerBlacklist,
    AdminJobRequest,
    JobFinishedReceipt,
    JobStartedReceipt,
    SystemEvent,
    Weights,
    Prompt,
    PromptSeries,
    PromptSample,
    SolveWorkload,
)  # noqa
from rangefilter.filters import DateTimeRangeFilter

from compute_horde.executor_class import EXECUTOR_CLASS
from compute_horde_validator.validator.tasks import trigger_run_admin_job_request  # noqa

admin.site.site_header = "ComputeHorde Validator Administration"
admin.site.site_title = "compute_horde_validator"
admin.site.index_title = "Welcome to ComputeHorde Validator Administration"

admin.site.index_template = "admin/validator_index.html"


class AddOnlyAdmin(admin.ModelAdmin):
    def has_change_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False


class ReadOnlyAdmin(AddOnlyAdmin):
    def has_add_permission(self, *args, **kwargs):
        return False


class AdminJobRequestForm(forms.ModelForm):
    executor_class = forms.ChoiceField()

    class Meta:
        model = AdminJobRequest
        fields = [
            "uuid",
            "miner",
            "executor_class",
            "docker_image",
            "timeout",
            "raw_script",
            "args",
            "use_gpu",
            "input_url",
            "output_url",
            "status_message",
        ]

    def __init__(self, *args, **kwargs):
        super(__class__, self).__init__(*args, **kwargs)
        if self.fields:
            # exclude blacklisted miners from valid results
            self.fields["miner"].queryset = Miner.objects.exclude(minerblacklist__isnull=False)
            self.fields["executor_class"].choices = [(name, name) for name in EXECUTOR_CLASS]


class AdminJobRequestAddOnlyAdmin(AddOnlyAdmin):
    form = AdminJobRequestForm
    exclude = ["env"]  # not used ?
    list_display = ["uuid", "executor_class", "docker_image", "use_gpu", "miner", "created_at"]
    readonly_fields = ["uuid", "status_message"]
    ordering = ["-created_at"]
    autocomplete_fields = ["miner"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        trigger_run_admin_job_request.delay(obj.id)
        organic_job = OrganicJob.objects.filter(job_uuid=obj.uuid).first()
        msg = (
            f"Please see <a href='/admin/validator/organicjob/{organic_job.pk}/change/'>ORGANIC JOB</a> for further details"
            if organic_job
            else f"Job {obj.uuid} failed to initialize"
        )
        messages.add_message(request, messages.INFO, mark_safe(msg))


class JobReadOnlyAdmin(ReadOnlyAdmin):
    list_display = ["job_uuid", "miner", "executor_class", "status", "updated_at"]
    search_fields = ["job_uuid", "miner__hotkey"]
    ordering = ["-updated_at"]


class MinerReadOnlyAdmin(ReadOnlyAdmin):
    change_form_template = "admin/read_only_view.html"
    search_fields = ["hotkey"]

    def has_add_permission(self, *args, **kwargs):
        return False

    # exclude blacklisted miners from autocomplete results
    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(
            request,
            queryset,
            search_term,
        )
        queryset = queryset.exclude(minerblacklist__isnull=False)
        return queryset, use_distinct


class SystemEventAdmin(ReadOnlyAdmin):
    list_display = ["type", "subtype", "timestamp"]
    list_filter = ["type", "subtype", ("timestamp", DateTimeRangeFilter)]
    ordering = ["-timestamp"]


class JobStartedReceiptsReadOnlyAdmin(ReadOnlyAdmin):
    list_display = [
        "job_uuid",
        "miner_hotkey",
        "validator_hotkey",
        "executor_class",
        "time_accepted",
        "max_timeout",
    ]
    ordering = ["-time_accepted"]


class JobFinishedReceiptsReadOnlyAdmin(ReadOnlyAdmin):
    list_display = [
        "job_uuid",
        "miner_hotkey",
        "validator_hotkey",
        "score",
        "time_started",
        "time_took",
    ]
    ordering = ["-time_started"]


class WeightsReadOnlyAdmin(ReadOnlyAdmin):
    list_display = ["block", "created_at", "revealed_at"]
    ordering = ["-created_at"]


class PromptSeriesAdmin(ReadOnlyAdmin):
    list_display = [
        "series_uuid",
        "s3_url",
        "created_at",
        "generator_version",
    ]


class SolveWorkloadAdmin(ReadOnlyAdmin):
    list_display = [
        "workload_uuid",
        "seed",
        "s3_url",
        "created_at",
        "finished_at",
    ]


class PromptSampleAdmin(ReadOnlyAdmin):
    list_display = [
        "pk",
        "series",
        "workload",
        "synthetic_job",
        "created_at",
    ]


class PromptAdmin(ReadOnlyAdmin):
    list_display = [
        "pk",
        "sample",
    ]


admin.site.register(Miner, admin_class=MinerReadOnlyAdmin)
admin.site.register(SyntheticJob, admin_class=JobReadOnlyAdmin)
admin.site.register(OrganicJob, admin_class=JobReadOnlyAdmin)
admin.site.register(JobFinishedReceipt, admin_class=JobFinishedReceiptsReadOnlyAdmin)
admin.site.register(JobStartedReceipt, admin_class=JobStartedReceiptsReadOnlyAdmin)
admin.site.register(MinerBlacklist)
admin.site.register(AdminJobRequest, admin_class=AdminJobRequestAddOnlyAdmin)
admin.site.register(SystemEvent, admin_class=SystemEventAdmin)
admin.site.register(Weights, admin_class=WeightsReadOnlyAdmin)
admin.site.register(PromptSeries, admin_class=PromptSeriesAdmin)
admin.site.register(SolveWorkload, admin_class=SolveWorkloadAdmin)
admin.site.register(PromptSample, admin_class=PromptSampleAdmin)
admin.site.register(Prompt, admin_class=PromptAdmin)
