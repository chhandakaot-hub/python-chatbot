# Every portal model is imported here so relationship() strings like "Course"
# resolve no matter which module is imported first.
from app.models.portal import catalog, feedback, reference, staff, student_extras  # noqa: F401
from app.models.portal.assignment import Assignment
from app.models.portal.course import Course
from app.models.portal.course_batch import CourseBatch
from app.models.portal.enrollment import Enrollment
from app.models.portal.result import Result
from app.models.portal.student import Student
from app.models.portal.student_assignment import StudentAssignment
from app.models.portal.topic import Topic

__all__ = [
    "Assignment",
    "Course",
    "CourseBatch",
    "Enrollment",
    "Result",
    "Student",
    "StudentAssignment",
    "Topic",
]
