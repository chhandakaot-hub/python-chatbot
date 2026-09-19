# Python Gemini Bot

A FastAPI chat backend with JWT authentication, persistent conversations, and
replies from Google Gemini.

## Requirements

- Python 3.12+ (developed on 3.14)
- MySQL — XAMPP's bundled server is fine
- A Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

Copy the settings into `.env` and fill in your own values:

```ini
APP_NAME=Python AI Bot
DATABASE_URL=mysql+pymysql://root:@127.0.0.1:3306/python_ai_bot
GEMINI_API_KEY=your_api_key_here
SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
AI_MODEL=gemini-flash-latest
AI_SYSTEM_PROMPT=You are a helpful assistant.
AI_TEMPERATURE=0.7
AI_MAX_TOKENS=8192
DEBUG=true
```

Generate a real secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Create the database (the app creates the *tables*, but never the database):

```sql
CREATE DATABASE python_ai_bot;
```

No MySQL handy? Point `DATABASE_URL` at `sqlite:///./python_ai_bot.db` instead.

## Running

```bash
uvicorn app.main:app --reload
```

- Chat UI: <http://127.0.0.1:8000/ui/> (`/` redirects here)
- Swagger UI: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

Tables are created on startup by `init_db()`.

## API

All endpoints are under `/api`. Everything except register and login needs an
`Authorization: Bearer <token>` header.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/register` | Create an account |
| POST | `/api/auth/login` | Exchange email + password for a token (JSON) |
| POST | `/api/auth/token` | Same, form-encoded, for the Swagger Authorize button |
| GET | `/api/users/me` | The current user |
| PATCH | `/api/users/me` | Update the current user |
| POST | `/api/chat` | Send a message, get a reply |
| GET | `/api/conversations` | List your conversations |
| GET | `/api/conversations/{id}` | One conversation with its messages |
| DELETE | `/api/conversations/{id}` | Delete a conversation |
| GET | `/api/students/search` | Students matching any combination of filters |
| GET | `/api/students/count` | True count of matches (not capped) |
| GET | `/api/students/breakdown?by=` | Counts grouped by country/state/city/status/occupation |
| GET | `/api/students/{id}` | One portal student |
| GET | `/api/students/{id}/enrollments` | Every enrollment for one student |
| GET | `/api/enrollments/search` | Enrollments matching any combination of filters |
| GET | `/api/enrollments/count` | True count of matching enrollments |
| GET | `/api/enrollments/breakdown?by=` | Counts by course/status/type/completed/is_certified/batch/month |
| GET | `/api/enrollments/{id}` | One enrollment in full |
| GET | `/api/courses/search?q=` | Find courses by partial name |
| GET | `/api/assignments/search` | Assignment definitions matching any filters |
| GET | `/api/assignments/count` | True count of matching assignments |
| GET | `/api/assignments/breakdown?by=` | Counts by course/topic/type/status/plagiarism/month |
| GET | `/api/assignments/{id}` | One assignment definition |
| GET | `/api/student-assignments/search` | Assignments as handed to students |
| GET | `/api/student-assignments/count` | True count of matching student assignments |
| GET | `/api/student-assignments/breakdown?by=` | Counts by status/course/mandatory/submits/topic/month |
| GET | `/api/student-assignments/{id}` | One student assignment |
| GET | `/api/enrollments/{id}/assignments` | Every assignment for one enrollment |
| GET | `/api/results/search` | Submissions with evaluation state and scores |
| GET | `/api/results/count` | True count of matching submissions |
| GET | `/api/results/breakdown?by=` | Counts by status/course/plagiarism/review/AI status/type/month |
| GET | `/api/results/{id}` | One submission in full |
| GET | `/api/students/{id}/results` | One student's current submissions |
| GET | `/api/completion/search` | Completion state per enrollment |
| GET | `/api/completion/count` | True count of matching enrollments |
| GET | `/api/completion/summary` | The three completion states plus certificates |
| GET | `/api/completion/breakdown?by=` | Counts by state/course/mcq_required/certified/month |
| GET | `/api/completion/{enrollment_id}` | One enrollment's completion in full |
| GET | `/api/completion/{enrollment_id}/progress` | Coursework handed in for one enrollment |
| GET | `/api/students/{id}/completion` | Completion state for every course a student takes |

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"supersecret1"}'

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"supersecret1"}' | jq -r .access_token)

curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Explain recursion in one sentence."}'
```

Omit `conversation_id` to start a new conversation; pass it to continue one.
The last `HISTORY_LIMIT` (default 20) messages are replayed as context on each
turn.

## Layout

```
static/            single-page chat UI, served by the app itself
app/
├── core/           config, database engine, password hashing + JWT
├── models/         SQLAlchemy tables: User, Conversation, Message
├── schemas/        Pydantic request/response models
├── routers/        HTTP endpoints — thin, they delegate to services
├── services/       business logic, including the Gemini client
└── dependencies/   shared FastAPI dependencies (get_current_user)
```

Routers hold no business logic; services hold no HTTP details beyond raising
`HTTPException`. Messages are stored with roles `user`/`assistant`/`system`, and
`gemini_service.build_contents()` translates those to Gemini's `user`/`model`
turns at the API boundary — so the database stays provider-neutral.

## Web UI

`static/index.html` is a dependency-free chat page (vanilla JS, no build step)
mounted at `/ui`. Because the app serves it, the browser and the API share an
origin, so no CORS configuration is involved. It covers sign-in and
registration, the conversation list, deleting a conversation, and the chat
itself, and keeps the token in `localStorage`. Assistant replies get a small
subset of markdown -- bold, italics, inline code and fenced code blocks --
rendered after the text has been HTML-escaped.

Prefer to serve it from Apache instead? Copy `static/` anywhere under
`htdocs/`, change `const API` at the top of the script to the full API origin
(`http://127.0.0.1:8000/api`), and add that origin to `CORS_ORIGINS`.

## The assignment portal connection

The bot reads the existing `assignmentportaluat` database so staff can ask
about students in plain English. That connection is **read-only and narrow**
by construction:

* `PORTAL_DATABASE_URL` is a separate engine with its own `PortalBase`, so
  `create_all()` can never touch portal tables.
* Every pooled connection runs `SET SESSION TRANSACTION READ ONLY`, so a stray
  write is refused by MariaDB itself (error 1792), not merely by convention.
* `app/models/portal/student.py` maps **13 of the table's 48 columns**. The
  unmapped ones -- `password`, `forum_pass`, `forum_access_token`,
  `edmingle_api_key`, `remember_token`, the OTP columns, and personal details
  like `date_of_birth`, `phone` and `address` -- are never named in a SELECT,
  so they cannot reach the API, a prompt, or a log. Tests in
  `tests/test_portal.py` fail if any of them is ever added.
* Searches are capped at `MAX_RESULTS = 25`. `count_students` returns the
  true total separately, so a capped page is never mistaken for a total.

Search, count and breakdown all accept the same optional criteria, built once
in `portal_service.build_filters()`: free text (name / email / reg code),
`city`, `state`, `country`, `status` (0 pending, 1 active, 2 disabled),
`current_activity`, `registered_after` / `registered_before`,
`last_login_after`, and `never_logged_in`.
* Portal SQL is never echoed, even with `DEBUG=true`.

To expose another table, repeat the four files: a model on `PortalBase` with
an explicit column list, a schema, service functions, and a router.

### Enrollments and courses

`enrollments` (one row per student per course, ~112k rows) is mapped with
`courses` and `course_batches` so answers carry course names and batch
labels, not bare ids. The same rules apply:

* `app/models/portal/enrollment.py` maps 22 of 53 columns. Not mapped: the
  free-text notes (`comment`, `pause_reason`, `paused_reason`,
  `deactivation_reason` -- personal circumstances, and a prompt-injection
  vector), order and package references, staff ids, and bulky internals like
  `passing_criteria` and `dashboard_journey_steps`.
* Relationships are `lazy="raise"`, so every join is explicit in
  `enrollment_service.py` and a stray attribute access cannot fire a query
  per row.
* Soft-deleted rows (`deleted_at`) are excluded from every query.

Integer codes are defined once in `app/models/portal/codes.py`, **copied from
the portal's Laravel constants** (`Modules/*/Entities/*.php`), and pinned by
tests:

| Column | Codes |
| --- | --- |
| `enrollments.status` | 0 pending, 1 active, 2 paused, 3 resume requested, 4 pause requested (refund eligible) |
| `enrollments.type` | 1 normal, 2 package, 3 bootcamp, 4 package batch |
| `courses.course_type` | 1 simple, 2 bootcamp |
| `students.status` | 0 pending, 1 active, 2 disabled |

API responses and breakdowns include a `status_label` / `type_label` next to
each code. Note that "paused" means `status = 2`: the `pause_status` column is
a history of pause requests (a resumed enrollment keeps its value) and is not
used to decide current state.

### Assignments, submissions and completion

Four more portal areas, on the same terms -- explicit column lists, row caps,
soft deletes excluded, integer codes labelled from the Laravel constants.

**Assignments** (`assignments`, ~2.7M rows) are the definitions a course sets:
code, topic, type, size, whether plagiarism is checked. 11 of 23 columns are
mapped. Not mapped: the instruction and sample-feedback links, the download
file and `allowed_file_types` (the assignment's own material), the AI model
settings, `package_id`, and the staff ids. `topics` is mapped as id and title
only, so an assignment can be named by its topic rather than its code.

Note `assignment_type = 0` is **subjective**, a real type covering about half
the table -- not "unset". Treating 0 as missing would make half the assignments
unfilterable.

**Student assignments** (`student_assignments`, ~4M rows, the largest table the
bot reads) are one assignment as handed to one enrollment: deadline, submit
counter, status. 11 of 17 columns are mapped; the per-student link overrides,
AI settings and staff ids are not.

The status codes matter here. 0 and 1 are *activation* states -- the assignment
is switched on for the student but nothing has been handed in -- and 4.35M of
the rows sit at 1. Only 3 (submitted), 4 (resubmitted) and 5 (evaluated) mean
work exists, so `submitted=true` filters on that set rather than on "not
deactivated", which would report nearly the whole portal as submitted.
`overdue=true` likewise means the deadline passed *and* nothing was handed in;
a past deadline on evaluated work is history.

**Results** (`results`, ~123k rows) are submissions and their evaluations.

> `results.assignment_id` points at **`student_assignments.id`**, not at
> `assignments.id`. The portal's own model says so
> (`Result::belongsTo(StudentAssignment::class, 'assignment_id')`), and all
> 123,355 rows join to `student_assignments` while 106,712 also happen to
> collide with an `assignments` id -- so the wrong join returns plausible rows
> about a different assignment instead of failing. Every query goes through
> `Result.student_assignment`, and the schema reports the column as
> `student_assignment_id`.

24 of 46 columns are mapped. Not mapped, and pinned by tests: every feedback
field (`ai_feedback`, `reviewer_edited_feedback`, `feedback_to_student`,
`resubmission_feedback`, `reason`, `feedback_edit_reason`) -- written about a
named student, and a prompt-injection vector; every file path and link; the
Unicheck and auto-assignment blobs; and the evaluator, reviewer and updater
ids. `score` is the reviewer's edited score when one exists and the AI score
otherwise, with both also reported separately. `latest_only` distinguishes
"how many submissions" from "how many assignments", since a resubmission
supersedes the row before it.

**Course completion** has no table of its own -- it is a verdict, and the
verdict is not the `completed` column:

    completed = 1                      -> passed the marks criteria
    + the course requires an LMS MCQ   -> not finished until mcq_completed = 1

That is the portal's own rule, from `CourseCompletionMasterResource::getCompleted`.
It is not an edge case: **53,910 enrollments require an MCQ, and reporting
`completed = 1` as finished gives 4,202 completions where the portal counts
2,198** -- an overstatement of 2,004, very nearly double. So
`/api/completion/*` reports three states that add up to the total:

| State | Meaning |
| --- | --- |
| `completed` | Finished -- criteria met, and the MCQ done if the course needs one |
| `awaiting_mcq` | Criteria met, LMS MCQ still unconfirmed |
| `not_completed` | Criteria not met |

`enrollment_service` still exposes the raw `completed` flag, which is the right
answer to a question about the column; the completion endpoints answer the
question about students finishing. The MCQ requirement is read from the one
`lms_mcq` key inside `enrollments.passing_criteria` with a server-side
`JSON_EXTRACT`; the blob itself stays unmapped and is never selected.
(`course_criterias` would be tidier but holds 2 rows for 215 courses, so it
would answer "no MCQ required" for almost everything.) The flag is stored four
ways -- `Y`, `N`, `1`, `0` -- plus NULL for the 29,482 enrollments with no
snapshot; anything not affirmative means no MCQ, which is the portal's own
fallback.

`/api/completion/{id}/progress` adds how much coursework was handed in, because
"not completed" alone does not say whether a student is one assignment short or
has not started.

### Query cost

Some of these tables are large enough that a careless join is the difference
between a fast answer and none. Three rules the services follow:

* **join only what the query needs.** Grouping results by `status` reads only
  `results`; it does not drag in `student_assignments` (4M rows) to do it.
  Adding the joins unconditionally cost 20+ seconds per breakdown against under
  half a second now, and made `/api/enrollments/count` take nine seconds where
  it now takes under one.
* **eager-load from the join that is already there.** `contains_eager()`, not
  `joinedload()`: where a relationship is joined for filtering, `joinedload`
  adds a *second*, aliased copy of the same join. Every list endpoint was
  emitting eight joins where four were needed.
* **bound the time, as MAX_RESULTS bounds the rows.** Every portal connection
  sets `max_statement_time` (`PORTAL_STATEMENT_TIMEOUT_SECONDS`, default 15s).
  A query that overruns is stopped by MariaDB and returned as **504** with a
  note to narrow the filters, rather than leaving a chat reply hanging or
  surfacing as an unexplained 500.

* **defer the join when ordering a big table.** `enrollments` is
  RANGE-partitioned by `created_at`; joining `courses` in the same statement
  makes the optimizer drive from `courses` and filesort every matching
  enrollment to find ten. `list_completions` picks the ids first and fetches
  those: 9.6s to 0.75s.

Measured on this machine, typical calls -- anything scoped to a student,
enrollment or course -- land between 5ms and 60ms, and the whole-portal
completion aggregates between 0.7s and 2s.

Two paths remain slow, and **no index fixes them**, which is worth writing down
because it is not the obvious conclusion:

* *student assignments filtered by course **name***. The existing
  `student_assignments(enrollment_id)` index is already used
  (`EXPLAIN` gives `type=ref rows=30`), and one enrollment's assignments come
  back in 9ms. The cost is the fanout: `%Contract%` matches 26,354 enrollments
  holding **1,390,072** student assignments, all of which must be produced and
  sorted to return ten. Rewriting the join as a subquery on `course_id` does
  not help either -- both forms exceed 45s. Filtering by `course_id` instead of
  a name takes 330ms, which is why `find_courses` exists: resolve the name to
  an id first.
* *results grouped by course or assignment type*, for the same reason -- every
  one of 123k results has to be joined out to its course before it can be
  grouped.

Both return 504 rather than hanging. An index on `enrollments(completed_at)`
would **not** have helped the completion list either: the sort was never the
bottleneck (the same ORDER BY runs in 572ms once the join is removed), the
driving-table choice was.

## How the AI reaches the portal

`AI_TOOLS_ENABLED=true` lets Gemini call twenty-nine functions declared in
`app/services/ai_tools.py`:

* students -- `search_students`, `count_students`, `breakdown_students`,
  `get_student`;
* enrollments -- `get_student_enrollments`, `search_enrollments`,
  `count_enrollments`, `breakdown_enrollments`, `get_enrollment`;
* courses -- `find_courses`;
* assignments -- `search_assignments`, `count_assignments`,
  `breakdown_assignments`, `get_assignment`;
* student assignments -- `search_student_assignments`,
  `count_student_assignments`, `breakdown_student_assignments`,
  `get_enrollment_assignments`;
* results -- `search_results`, `count_results`, `breakdown_results`,
  `get_result`, `get_student_results`;
* completion -- `completion_summary`, `search_completions`,
  `count_completions`, `breakdown_completions`, `get_completion`,
  `get_student_completion`.

The completion tools carry an explicit instruction to prefer them over the
enrollment tools' `completed` filter for any question about finishing a course,
because that filter answers a subtly different question.

A question about a named student typically chains two calls:
`search_students` to find the id, then `get_student_enrollments`. The model picks the arguments; it never writes SQL, and a
function name it invents is refused in `dispatch()` before anything runs, and
so is an invented *parameter* name -- silently ignoring one would turn "how
many students in Delhi" into the count of every student, reported as the
answer.

`generate_reply()` runs the loop by hand rather than using the SDK's automatic
calling, so every call is logged (names only, never rows) and bounded by
`MAX_TOOL_ROUNDS`. Set `AI_TOOLS_ENABLED=false` to turn the portal access off
and leave a plain chatbot.

Two things to keep in mind: rows sent as tool results **are sent to Google**,
so the column allow-list is also the list of what leaves your servers; and
database text is untrusted -- a student whose name field contains instructions
is data, not a command, which is what the system prompt tells the model.

## Tests

```bash
pytest
```

139 tests run against an in-memory SQLite database with the Gemini call
stubbed, so they need neither MySQL nor an API key. The portal tests never
connect either: they pin the column allow-lists, the codes copied from the
Laravel constants, the grouping allow-lists and the query shapes.

### Reply length and the tool loop

Two limits decide whether a long answer arrives intact:

* **`AI_MAX_TOKENS` (default 8192).** Gemini counts its *thinking* tokens
  against this budget, not just the visible answer -- measured at ~280 thinking
  tokens for a trivial twelve-row table. At the old default of 1024 any answer
  containing a real table stopped mid-row, and the caller could not tell,
  because a truncated reply reads like a finished one. `generate_reply()` now
  checks `finish_reason` and appends a line saying the answer was cut short.
  `AI_THINKING_BUDGET=0` turns thinking off and hands the whole budget to the
  answer.
* **`MAX_TOOL_ROUNDS` (8).** It counts *model turns*, and N rounds of tools
  needs N+1 turns to produce the closing text, so the deepest chain it allows
  is seven. One question about a student really does chain four or five: find
  the student, fetch completion, enrollments and assignments, then the
  submissions. At a cap of 5 that tipped over at random, because the model
  sometimes takes one extra step.

  If the rounds do run out, the last turn is made with tool calling **refused**
  (`tool_config` mode `NONE`), not merely with `tools` unset -- with a
  transcript full of function calls behind it the model copies the pattern and
  emits another call, which leaves `response.text` empty and surfaced as a
  bogus "AI provider returned an empty response".

The Gemini client is cached **per event loop**, not per process: its async
transport binds its connection pool to the loop that first used it, so a
process-wide singleton raises `RuntimeError: Event loop is closed` the moment a
second loop uses it. Under uvicorn there is one long-lived loop and this is
identical to a singleton; in tests and scripts it is the difference between
working and not.

## Notes

- **Passwords** are hashed with PBKDF2-SHA256 and **tokens** signed with
  HMAC-SHA256, both from the standard library — no `passlib`/`python-jose`
  needed. To swap those in, replace the four functions in `app/core/security.py`.
- **`migrations/`** is an empty placeholder. Schema changes are currently
  applied by `create_all()` on startup, which creates missing tables but will
  not alter existing ones. Wire up Alembic before changing a model in anger.
- **Not built yet:** refresh tokens, logout/revocation, streaming replies,
  rate limiting.
