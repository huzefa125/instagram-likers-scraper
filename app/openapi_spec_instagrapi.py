"""OpenAPI 3.0 spec for the instagrapi comparison API (app/api_instagrapi.py),
served at /openapi.json and rendered by Swagger UI at /docs on that server's
own port. Kept separate from app/openapi_spec.py the same way the server
itself is separate - different method (mobile-API emulation, not a real
browser), different risk profile, different Python environment.
"""

SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Instagram Scraper API - instagrapi comparison",
        "version": "1.0.0",
        "description": (
            "A side-by-side comparison implementation of the main pipeline's data (profile, "
            "posts, comments, likers, followers), built with instagrapi instead of real browser "
            "automation. instagrapi emulates Instagram's private MOBILE APP API directly (signed "
            "requests, device IDs) rather than driving a real Chrome browser - the same category "
            "of technique HikerAPI and similar commercial scrapers use, and the same category that "
            "got an account flagged earlier in this project (which is why the main API at a "
            "different port uses genuine browser automation instead). This server exists to "
            "measure the tradeoff (speed and completeness vs. detection risk), not as a "
            "replacement for the main API - treat every call here as spending some of the "
            "session's account trust, not a free read.\n\n"
            "Runs under its own Python (.venv-instagrapi/, Python 3.14) since instagrapi's "
            "pydantic-v2 dependency has no prebuilt wheel for this project's main Python (3.15 "
            "beta) and fails to compile from source there - a separate venv, a separate process, "
            "a separate port, on purpose."
        ),
    },
    "servers": [{"url": "/"}],
    "security": [{"ApiKeyAuth": []}],
    "tags": [
        {"name": "meta", "description": "Health check"},
        {"name": "scrape", "description": "Run a comparison scrape (synchronous - seconds, not minutes)"},
    ],
    "paths": {
        "/health": {
            "get": {
                "tags": ["meta"],
                "summary": "Server connectivity check",
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Health"}}},
                    }
                },
            }
        },
        "/scrape/{username}": {
            "post": {
                "tags": ["scrape"],
                "summary": "Scrape an account via instagrapi - synchronous, returns the full result directly",
                "description": (
                    "Unlike the main API's POST /scrape/<username> (background job + polling, since "
                    "a real browser session takes minutes), this is synchronous: instagrapi calls "
                    "are plain signed HTTP requests and typically finish in seconds, so the full "
                    "result comes back in this one response - no job id, no GET /jobs/{job_id}."
                ),
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}, "example": "nasa"}
                ],
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/InstagrapiScrapeOptions"}}},
                },
                "responses": {
                    "200": {
                        "description": "Full scrape result",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/InstagrapiResult"}}},
                    },
                    "502": {
                        "description": "instagrapi call failed (bad/expired session, account challenged, rate-limited, etc.)",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/InstagrapiError"}}},
                    },
                },
            }
        },
    },
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey", "in": "header", "name": "X-API-Key",
                "description": (
                    "Required on every endpoint except /health and /docs when INSTAGRAPI_API_KEY "
                    "is set in the server's environment. Unset (local dev default) means no auth "
                    "is enforced - always set this before deploying anywhere reachable from the "
                    "internet, since every call here spends real trust on a real Instagram account."
                ),
            },
        },
        "schemas": {
            "Health": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "method": {"type": "string", "example": "instagrapi"}},
            },
            "InstagrapiScrapeOptions": {
                "type": "object",
                "description": "Mirrors the main API's ScrapeOptions field-for-field where the underlying instagrapi call supports it.",
                "properties": {
                    "posts": {"type": "integer", "default": 5, "example": 5, "description": "How many posts/reels to process (most recent, or date-matching - see postsMonth)."},
                    "profile": {"type": "boolean", "default": True, "example": True, "description": "Fetch profile info (bio, counts, verified, etc.). On by default."},
                    "followers": {"type": "boolean", "default": True, "example": True, "description": "Fetch followers. On by default."},
                    "followersLimit": {"type": "integer", "default": 50, "example": 50, "description": "How many followers to fetch."},
                    "following": {"type": "boolean", "default": False, "example": False, "description": "Fetch who this account follows. Off by default, same as the main API."},
                    "followingLimit": {"type": "integer", "default": 50, "example": 50, "description": "How many following-list entries to fetch."},
                    "likers": {"type": "boolean", "default": True, "example": True, "description": "Fetch likers on each processed post/reel. On by default."},
                    "likersLimit": {
                        "type": "integer", "nullable": True, "example": 20,
                        "description": (
                            "Cap on likers kept *per post*. instagrapi's media_likers call has no "
                            "server-side amount param (unlike comments/followers/following), so this "
                            "is applied as a post-hoc slice, same as the browser pipeline does. Omit/"
                            "null for however many Instagram's endpoint returns in one call."
                        ),
                    },
                    "comments": {"type": "boolean", "default": True, "example": True, "description": "Fetch comments on each processed post/reel. On by default."},
                    "commentsLimit": {"type": "integer", "default": 20, "example": 20, "description": "How many comments to fetch *per post* (server-side amount param)."},
                    "postsMonth": {
                        "type": "string", "nullable": True, "example": "2026-09",
                        "description": (
                            "\"YYYY-MM\" - only process posts actually taken in that calendar month "
                            "(UTC). \"posts\" still caps how many matching posts get processed. Takes "
                            "priority over postsFrom/postsTo."
                        ),
                    },
                    "postsFrom": {"type": "number", "nullable": True, "description": "Unix timestamp (seconds); lower bound of a custom post-date range, used instead of postsMonth."},
                    "postsTo": {"type": "number", "nullable": True, "description": "Unix timestamp (seconds); upper bound of a custom post-date range, used instead of postsMonth."},
                    "storeMongo": {
                        "type": "boolean", "default": True, "example": True,
                        "description": (
                            "Write results to MongoDB - the same collections/documents the browser "
                            "pipeline writes to (compatible shapes by design), tagged "
                            "source=\"instagrapi\" so records stay distinguishable by which method "
                            "produced them. On by default."
                        ),
                    },
                    "account": {
                        "type": "string", "nullable": True, "example": "acc1",
                        "description": (
                            "Named login account (matches IG_SESSIONID_<NAME> in .env, same convention "
                            "as the main API). Falls back to the default IG_SESSIONID when omitted."
                        ),
                    },
                },
                "example": {
                    "posts": 5,
                    "profile": True,
                    "followers": True,
                    "followersLimit": 50,
                    "following": False,
                    "followingLimit": 50,
                    "likers": True,
                    "likersLimit": 20,
                    "comments": True,
                    "commentsLimit": 20,
                    "postsMonth": "2026-09",
                    "account": "acc1",
                },
            },
            "InstagrapiError": {
                "type": "object",
                "properties": {"error": {"type": "string"}, "elapsedSeconds": {"type": "number"}},
            },
            "InstagrapiProfile": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "userId": {"type": "string"},
                    "fullName": {"type": "string"},
                    "biography": {"type": "string"},
                    "externalUrl": {"type": "string", "nullable": True},
                    "isVerified": {"type": "boolean"},
                    "isPrivate": {"type": "boolean"},
                    "isBusinessAccount": {"type": "boolean"},
                    "category": {"type": "string", "nullable": True},
                    "profilePicUrl": {"type": "string", "nullable": True},
                    "postsCount": {"type": "integer"},
                    "followersCount": {"type": "integer"},
                    "followingCount": {"type": "integer"},
                },
            },
            "InstagrapiPost": {
                "type": "object",
                "properties": {
                    "mediaId": {"type": "string"},
                    "shortcode": {"type": "string"},
                    "mediaType": {"type": "string", "enum": ["post", "reel"]},
                    "productType": {"type": "string"},
                    "takenAt": {"type": "integer", "nullable": True, "description": "Unix timestamp."},
                    "likeCount": {"type": "integer"},
                    "commentCount": {"type": "integer"},
                    "caption": {"type": "string"},
                },
            },
            "InstagrapiComment": {
                "type": "object",
                "properties": {
                    "commentId": {"type": "string"},
                    "profileId": {"type": "string"},
                    "username": {"type": "string"},
                    "fullName": {"type": "string"},
                    "text": {"type": "string"},
                    "likeCount": {"type": "integer"},
                    "createdAt": {"type": "integer", "nullable": True},
                },
            },
            "InstagrapiLiker": {
                "type": "object",
                "properties": {
                    "profileId": {"type": "string"},
                    "username": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string", "nullable": True},
                },
            },
            "InstagrapiFollower": {
                "type": "object",
                "properties": {
                    "profileId": {"type": "string"},
                    "username": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string", "nullable": True},
                },
            },
            "InstagrapiFollowing": {
                "type": "object",
                "properties": {
                    "profileId": {"type": "string"},
                    "username": {"type": "string"},
                    "fullName": {"type": "string"},
                    "isPrivate": {"type": "boolean"},
                    "pictureUrl": {"type": "string", "nullable": True},
                },
            },
            "InstagrapiResult": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "method": {"type": "string", "example": "instagrapi"},
                    "elapsedSeconds": {"type": "number", "description": "Wall-clock time for the whole call - typically single-digit to low-double-digit seconds."},
                    "profile": {"$ref": "#/components/schemas/InstagrapiProfile", "nullable": True, "description": "null if \"profile\": false was passed."},
                    "posts": {"type": "array", "items": {"$ref": "#/components/schemas/InstagrapiPost"}},
                    "commentsCount": {"type": "integer", "nullable": True},
                    "likersCount": {"type": "integer", "nullable": True},
                    "followersCount": {"type": "integer", "nullable": True},
                    "followingCount": {"type": "integer", "nullable": True},
                    "comments": {"type": "array", "items": {"$ref": "#/components/schemas/InstagrapiComment"}},
                    "likers": {"type": "array", "items": {"$ref": "#/components/schemas/InstagrapiLiker"}},
                    "followers": {"type": "array", "items": {"$ref": "#/components/schemas/InstagrapiFollower"}},
                    "following": {"type": "array", "items": {"$ref": "#/components/schemas/InstagrapiFollowing"}},
                    "mongoError": {
                        "type": "string", "nullable": True,
                        "description": "Present only if storeMongo was true and the write failed - the scrape itself still succeeded and is returned above.",
                    },
                },
            },
        },
    },
}
