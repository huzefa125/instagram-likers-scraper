"""Hand-written OpenAPI 3.0 spec for the API, served at /openapi.json and
rendered by Swagger UI at /docs. Kept as a plain dict (not auto-generated
from decorators) so it stays exact and doesn't depend on any introspection
library - one less thing that could fail to install on this machine's beta
Python.
"""

SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Instagram Scraper API",
        "version": "1.0.0",
        "description": (
            "Scrapes an Instagram account's profile info, followers, following list, "
            "post/reel comments, and post/reel likers via browser automation, storing "
            "results in MongoDB. Scrapes run as background jobs (POST /scrape/<username>) "
            "since each one drives a real Chrome browser and can take minutes."
        ),
    },
    "servers": [{"url": "/"}],
    "tags": [
        {"name": "meta", "description": "Health checks"},
        {"name": "jobs", "description": "Start and monitor scrape jobs"},
        {"name": "data", "description": "Read stored scrape results"},
    ],
    "paths": {
        "/health": {
            "get": {
                "tags": ["meta"],
                "summary": "Server + MongoDB connectivity check",
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Health"}}},
                    }
                },
            }
        },
        "/accounts": {
            "get": {
                "tags": ["meta"],
                "summary": "List configured named login accounts",
                "description": (
                    "Named accounts come from IG_SESSIONID_<NAME> entries in .env. Pass one of "
                    "these names as \"account\" in a POST /scrape body to run that job logged in "
                    "as that account, instead of the default IG_SESSIONID - this is what lets "
                    "several scrapes run at once without sharing (and rate-limiting/flagging) "
                    "one Instagram session. Never returns the actual sessionid values."
                ),
                "responses": {
                    "200": {
                        "description": "Configured account names",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"accounts": {"type": "array", "items": {"type": "string"}}},
                        }}},
                    }
                },
            }
        },
        "/accounts/status": {
            "get": {
                "tags": ["meta"],
                "summary": "Pool visibility: busy/free, last-used time, job count per named account",
                "description": (
                    "Confirms load is actually spreading across the account pool rather than "
                    "piling onto a few. Never returns the actual sessionid values."
                ),
                "responses": {
                    "200": {
                        "description": "Per-account pool status",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"accounts": {"type": "array", "items": {"$ref": "#/components/schemas/AccountStatus"}}},
                        }}},
                    }
                },
            }
        },
        "/scrape/{username}": {
            "post": {
                "tags": ["jobs"],
                "summary": "Start a scrape job for an Instagram account",
                "description": (
                    "Runs in the background (concurrency capped server-wide - see "
                    "IG_MAX_CONCURRENT_JOBS - since each job drives a real browser). Returns a "
                    "job id immediately; poll GET /jobs/{job_id} for status and results. If any "
                    "named accounts are configured (see GET /accounts) and \"account\" is omitted, "
                    "one is auto-assigned from the pool instead of using the shared default "
                    "IG_SESSIONID for every job."
                ),
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"},
                     "example": "nasa"}
                ],
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ScrapeOptions"}}},
                },
                "responses": {
                    "202": {
                        "description": "Job queued",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobQueued"}}},
                    }
                },
            }
        },
        "/scrape/bulk": {
            "post": {
                "tags": ["jobs"],
                "summary": "Queue a scrape job per username - fan out across many influencers at once",
                "description": (
                    "Body is the same as POST /scrape/<username>'s ScrapeOptions plus a required "
                    "\"usernames\" array; every username gets its own job, auto-assigned a free "
                    "account from the pool (see GET /accounts/status) unless \"account\" is "
                    "explicitly pinned in the body (in which case every job in the batch shares "
                    "that one account - usually not what you want for a bulk call). This is the "
                    "intended entry point for scraping a large list of influencers."
                ),
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/BulkScrapeRequest"}}},
                },
                "responses": {
                    "202": {
                        "description": "Jobs queued",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/BulkJobsQueued"}}},
                    },
                    "400": {"description": "\"usernames\" missing or empty"},
                },
            }
        },
        "/jobs/{job_id}": {
            "get": {
                "tags": ["jobs"],
                "summary": "Get a job's status/progress/result",
                "parameters": [
                    {"name": "job_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}
                ],
                "responses": {
                    "200": {
                        "description": "Job record",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Job"}}},
                    },
                    "404": {"description": "Job not found"},
                },
            }
        },
        "/jobs": {
            "get": {
                "tags": ["jobs"],
                "summary": "List recent jobs",
                "parameters": [
                    {"name": "username", "in": "query", "schema": {"type": "string"},
                     "description": "Filter to one account's jobs."},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20, "maximum": 100}},
                ],
                "responses": {
                    "200": {
                        "description": "Job history, newest first",
                        "content": {"application/json": {"schema": {
                            "type": "array", "items": {"$ref": "#/components/schemas/Job"}
                        }}},
                    }
                },
            }
        },
        "/followers/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored followers for an account",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/limit"},
                    {"$ref": "#/components/parameters/skip"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated followers",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FollowersPage"}}},
                    }
                },
            }
        },
        "/following/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored following list for an account (who they follow)",
                "description": (
                    "Not scraped by default - pass \"following\": true in the POST /scrape body "
                    "first (see ScrapeOptions), otherwise this returns an empty page."
                ),
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/limit"},
                    {"$ref": "#/components/parameters/skip"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated following list",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FollowingPage"}}},
                    }
                },
            }
        },
        "/profile/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored profile info for an account",
                "description": (
                    "Full name, bio, external-link text, verified/private flags, profile picture, "
                    "and follower/following/post counts - scraped by default on every POST /scrape "
                    "call (see the \"profile\" option in ScrapeOptions to opt out)."
                ),
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}, "example": "nasa"},
                ],
                "responses": {
                    "200": {
                        "description": "Profile info",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Profile"}}},
                    },
                    "404": {"description": "No profile data scraped yet for this account"},
                },
            }
        },
        "/comments/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored comments across an account's scraped posts/reels",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/limit"},
                    {"$ref": "#/components/parameters/skip"},
                    {"$ref": "#/components/parameters/mediaType"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated comments",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CommentsPage"}}},
                    }
                },
            }
        },
        "/likers/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored likers across an account's scraped posts/reels",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/limit"},
                    {"$ref": "#/components/parameters/skip"},
                    {"$ref": "#/components/parameters/mediaType"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated likers",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LikersPage"}}},
                    }
                },
            }
        },
        "/posts/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Stored posts/reels for an account, date-wise (newest first)",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/limit"},
                    {"$ref": "#/components/parameters/skip"},
                    {"$ref": "#/components/parameters/mediaType"},
                    {"name": "sort", "in": "query", "schema": {"type": "string", "default": "takenAt"},
                     "description": "Field to sort by, descending."},
                    {"$ref": "#/components/parameters/month"},
                    {"$ref": "#/components/parameters/dateFrom"},
                    {"$ref": "#/components/parameters/dateTo"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated posts/reels",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PostsPage"}}},
                    }
                },
            }
        },
        "/account/{username}": {
            "get": {
                "tags": ["data"],
                "summary": "Everything for an account in one call: profile, followers, following, posts, comments, likers, and the latest job status",
                "description": (
                    "The all-in-one read endpoint - instead of separately calling /jobs, /profile, "
                    "/followers, /following, /posts, /comments, and /likers, get everything for an "
                    "account in a single response."
                ),
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}, "example": "nasa"},
                    {"$ref": "#/components/parameters/mediaType"},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 100, "maximum": 1000},
                     "description": "Default cap applied to every section unless overridden below."},
                    {"name": "followersLimit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "followingLimit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "postsLimit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "commentsLimit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "likersLimit", "in": "query", "schema": {"type": "integer"}},
                    {"$ref": "#/components/parameters/month"},
                    {"$ref": "#/components/parameters/dateFrom"},
                    {"$ref": "#/components/parameters/dateTo"},
                ],
                "responses": {
                    "200": {
                        "description": "Combined account data",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AccountData"}}},
                    }
                },
            }
        },
    },
    "components": {
        "parameters": {
            "limit": {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 100, "maximum": 1000}},
            "skip": {"name": "skip", "in": "query", "schema": {"type": "integer", "default": 0}},
            "mediaType": {
                "name": "type", "in": "query", "schema": {"type": "string", "enum": ["post", "reel"]},
                "description": "Filter to photo/carousel posts or Reels only.",
            },
            "month": {
                "name": "month", "in": "query", "schema": {"type": "string", "example": "2026-09"},
                "description": (
                    "Restrict to that calendar month (UTC) - posts by takenAt, comments by "
                    "createdAt. e.g. month=2026-09 answers \"how many posts this month\" via "
                    "the response's \"total\". Takes priority over from/to if both are given."
                ),
            },
            "dateFrom": {
                "name": "from", "in": "query", "schema": {"type": "integer"},
                "description": "Unix timestamp (seconds); lower bound of a custom date range, used instead of month.",
            },
            "dateTo": {
                "name": "to", "in": "query", "schema": {"type": "integer"},
                "description": "Unix timestamp (seconds); upper bound of a custom date range, used instead of month.",
            },
        },
        "schemas": {
            "Health": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "mongo": {"type": "boolean"}},
            },
            "ScrapeOptions": {
                "type": "object",
                "properties": {
                    "posts": {"type": "integer", "default": 5, "example": 5, "description": "How many recent (or date-matching, see postsMonth) posts/reels to process."},
                    "followers": {"type": "boolean", "default": True, "example": True, "description": "Scrape followers (see GET /followers). On by default."},
                    "following": {
                        "type": "boolean", "default": False, "example": False,
                        "description": (
                            "Scrape who this account follows (see GET /following). Off by default, "
                            "unlike followers - it's a separate dialog scrape at the same cost as "
                            "followers, so it's opt-in rather than bundled into every run."
                        ),
                    },
                    "likers": {"type": "boolean", "default": True, "example": True, "description": "Scrape likers on each processed post/reel (see GET /likers). On by default."},
                    "comments": {"type": "boolean", "default": True, "example": True, "description": "Scrape comments on each processed post/reel (see GET /comments). On by default."},
                    "profile": {
                        "type": "boolean", "default": True, "example": True,
                        "description": (
                            "Scrape profile info: full name, bio, external link, verified flag, "
                            "profile picture, and follower/following/post counts (see GET /profile). "
                            "One extra cheap page load - on by default."
                        ),
                    },
                    "headless": {"type": "boolean", "default": True, "example": True, "description": "Run Chrome headless (no visible window). On by default."},
                    "maxIterations": {"type": "integer", "default": 60, "example": 60, "description": "Max scroll/load-more rounds per section (followers/following/likers/comments)."},
                    "pause": {"type": "number", "default": 1.5, "example": 1.5, "description": "Seconds to wait between scroll/load-more rounds."},
                    "storeMongo": {"type": "boolean", "default": True, "example": True, "description": "Write results to MongoDB. On by default."},
                    "writeFiles": {"type": "boolean", "default": False, "example": False, "description": "Also write CSV/JSON snapshots to the data/ folder on disk. Off by default."},
                    "followersLimit": {"type": "integer", "nullable": True, "example": 50, "description": "Cap on followers kept. Omit/null for unlimited."},
                    "followingLimit": {"type": "integer", "nullable": True, "example": 50, "description": "Cap on following-list entries kept. Omit/null for unlimited."},
                    "commentsLimit": {"type": "integer", "nullable": True, "example": 20, "description": "Cap on comments kept *per post*. Omit/null for unlimited."},
                    "likersLimit": {"type": "integer", "nullable": True, "example": 20, "description": "Cap on likers kept *per post*. Omit/null for unlimited."},
                    "postsMonth": {
                        "type": "string", "nullable": True, "example": "2026-09",
                        "description": (
                            "\"YYYY-MM\" - only process posts actually taken in that calendar month "
                            "(UTC), instead of just the N most recent. \"posts\" still caps how many "
                            "*matching* posts get processed - raise it if you want more than the "
                            "default 5 from that month. Takes priority over postsFrom/postsTo."
                        ),
                    },
                    "postsFrom": {
                        "type": "number", "nullable": True,
                        "description": "Unix timestamp (seconds); lower bound of a custom post-date range, used instead of postsMonth.",
                    },
                    "postsTo": {
                        "type": "number", "nullable": True,
                        "description": "Unix timestamp (seconds); upper bound of a custom post-date range, used instead of postsMonth.",
                    },
                    "account": {
                        "type": "string", "nullable": True, "example": "acc1",
                        "description": (
                            "Named login account to run this job as (see GET /accounts). If omitted "
                            "and any named accounts are configured, one is auto-assigned from the "
                            "pool instead of falling back to the default IG_SESSIONID."
                        ),
                    },
                },
                "example": {
                    "posts": 5,
                    "followers": True,
                    "following": False,
                    "likers": True,
                    "comments": True,
                    "profile": True,
                    "headless": True,
                    "maxIterations": 60,
                    "pause": 1.5,
                    "storeMongo": True,
                    "writeFiles": False,
                    "followersLimit": 50,
                    "followingLimit": 50,
                    "commentsLimit": 20,
                    "likersLimit": 20,
                    "postsMonth": "2026-09",
                    "account": "acc1",
                },
            },
            "JobQueued": {
                "type": "object",
                "properties": {"jobId": {"type": "string", "format": "uuid"}, "status": {"type": "string", "example": "queued"}},
            },
            "AccountStatus": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "busy": {"type": "boolean"},
                    "lastUsedAt": {"type": "string", "format": "date-time", "nullable": True},
                    "jobCount": {"type": "integer"},
                },
            },
            "BulkScrapeRequest": {
                "allOf": [
                    {"$ref": "#/components/schemas/ScrapeOptions"},
                    {
                        "type": "object",
                        "required": ["usernames"],
                        "properties": {
                            "usernames": {
                                "type": "array", "items": {"type": "string"},
                                "description": "Instagram usernames to scrape, one job each.",
                            },
                        },
                    },
                ],
                "example": {"usernames": ["nasa", "spacex", "esa"], "posts": 5, "commentsLimit": 20, "likersLimit": 20},
            },
            "BulkJobsQueued": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"username": {"type": "string"}, "jobId": {"type": "string", "format": "uuid"}},
                        },
                    },
                },
            },
            "Job": {
                "type": "object",
                "properties": {
                    "jobId": {"type": "string"},
                    "username": {"type": "string"},
                    "status": {"type": "string", "enum": ["queued", "running", "done", "failed"]},
                    "options": {"$ref": "#/components/schemas/ScrapeOptions"},
                    "queuedAt": {"type": "string", "format": "date-time"},
                    "startedAt": {"type": "string", "format": "date-time"},
                    "finishedAt": {"type": "string", "format": "date-time"},
                    "progress": {"type": "array", "items": {"type": "string"}},
                    "result": {"$ref": "#/components/schemas/PipelineResult"},
                    "error": {"type": "string"},
                },
            },
            "PipelineResult": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "profile": {"$ref": "#/components/schemas/Profile"},
                    "shortcodesUsed": {"type": "array", "items": {"type": "string"}},
                    "posts": {"type": "array", "items": {"$ref": "#/components/schemas/PostMeta"}},
                    "followersCount": {"type": "integer", "nullable": True},
                    "followingCount": {"type": "integer", "nullable": True},
                    "likersCount": {"type": "integer", "nullable": True},
                    "commentsCount": {"type": "integer", "nullable": True},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
            },
            "PostMeta": {
                "type": "object",
                "properties": {
                    "mediaId": {"type": "string"},
                    "shortcode": {"type": "string"},
                    "mediaType": {"type": "string", "enum": ["post", "reel"]},
                    "productType": {"type": "string"},
                    "takenAt": {"type": "integer", "description": "Unix timestamp."},
                    "likeCount": {"type": "integer"},
                    "commentCount": {"type": "integer"},
                    "caption": {"type": "string"},
                },
            },
            "Follower": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "profileId": {"type": "string"},
                    "followerUsername": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string"},
                    "capturedAt": {"type": "string", "format": "date-time"},
                },
            },
            "Following": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "profileId": {"type": "string"},
                    "followingUsername": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string"},
                    "capturedAt": {"type": "string", "format": "date-time"},
                },
            },
            "Profile": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "fullName": {"type": "string"},
                    "biography": {"type": "string"},
                    "externalUrl": {
                        "type": "string", "nullable": True,
                        "description": "Raw text as shown on the profile (e.g. \"www.nasa.gov and 4 more\"), not a clean URL.",
                    },
                    "isVerified": {"type": "boolean"},
                    "isPrivate": {"type": "boolean"},
                    "profilePicUrl": {"type": "string", "nullable": True},
                    "postsCount": {"type": "integer", "nullable": True},
                    "followersCount": {"type": "integer", "nullable": True},
                    "followingCount": {"type": "integer", "nullable": True},
                    "capturedAt": {"type": "string", "format": "date-time"},
                },
            },
            "Comment": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "mediaId": {"type": "string"},
                    "mediaType": {"type": "string", "enum": ["post", "reel"], "nullable": True},
                    "commentId": {"type": "string"},
                    "profileId": {"type": "string"},
                    "commenterUsername": {"type": "string"},
                    "fullName": {"type": "string"},
                    "text": {"type": "string"},
                    "likeCount": {"type": "integer"},
                    "createdAt": {"type": "integer", "description": "Unix timestamp."},
                    "isPrivate": {"type": "boolean"},
                    "capturedAt": {"type": "string", "format": "date-time"},
                },
            },
            "Liker": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "mediaId": {"type": "string"},
                    "mediaType": {"type": "string", "enum": ["post", "reel"], "nullable": True},
                    "profileId": {"type": "string", "nullable": True, "description": "Not always available (DOM-scraped)."},
                    "likerUsername": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string", "nullable": True},
                    "capturedAt": {"type": "string", "format": "date-time"},
                },
            },
            "FollowersPage": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "skip": {"type": "integer"},
                    "sort": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Follower"}},
                },
            },
            "FollowingPage": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "skip": {"type": "integer"},
                    "sort": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Following"}},
                },
            },
            "CommentsPage": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "skip": {"type": "integer"},
                    "sort": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Comment"}},
                },
            },
            "LikersPage": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "skip": {"type": "integer"},
                    "sort": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Liker"}},
                },
            },
            "PostsPage": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"}, "limit": {"type": "integer"}, "skip": {"type": "integer"},
                    "sort": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/PostMeta"}},
                },
            },
            "AccountData": {
                "type": "object",
                "description": "Everything for an account: profile, followers, following, posts, comments, likers, and the latest scrape job.",
                "properties": {
                    "username": {"type": "string"},
                    "latestJob": {"$ref": "#/components/schemas/Job"},
                    "profile": {"$ref": "#/components/schemas/Profile"},
                    "counts": {
                        "type": "object",
                        "properties": {
                            "followers": {"type": "integer"},
                            "following": {"type": "integer"},
                            "posts": {"type": "integer"},
                            "comments": {"type": "integer"},
                            "likers": {"type": "integer"},
                        },
                    },
                    "followers": {"$ref": "#/components/schemas/FollowersPage"},
                    "following": {"$ref": "#/components/schemas/FollowingPage"},
                    "posts": {"$ref": "#/components/schemas/PostsPage"},
                    "comments": {"$ref": "#/components/schemas/CommentsPage"},
                    "likers": {"$ref": "#/components/schemas/LikersPage"},
                },
            },
        },
    },
}
