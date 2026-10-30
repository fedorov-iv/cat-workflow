from django.contrib import admin

from .models import CourseUserStatus, Course, LearningFlow, OrgDocument, Project, TicketApplication, Transfert


class CourseInline(admin.TabularInline):
    model = Course
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'is_expelling_prohibited')
    inlines = [CourseInline]


class FlowInline(admin.TabularInline):
    model = LearningFlow
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'project')
    inlines = [FlowInline]


@admin.register(TicketApplication)
class TicketApplicationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'course', 'project', 'status_title', 'status')
    list_filter = ('project', 'status')
    raw_id_fields = ('transfert', 'reason_document')

    @admin.display(description='Статус проекта')
    def status_title(self, obj):
        return obj.status_title


admin.site.register(Transfert)
admin.site.register(OrgDocument)
admin.site.register(LearningFlow)
admin.site.register(CourseUserStatus)
