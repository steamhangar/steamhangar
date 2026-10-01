# SteamHangar app — release ProGuard/R8 rules.
# Empty on purpose: release shrinking is OFF (app/build.gradle.kts sets
# isMinifyEnabled = false; WP 4b.9 shipped the release build without turning
# it on). This file is still referenced by proguardFiles so a future change
# that enables minification has a place for its keep rules (kotlinx
# serialization DTOs, OkHttp) — add them here together with that change,
# never before it (WP APP-FIX-1 N1 corrected the stale "once 4b.9 turns it
# on" wording).
