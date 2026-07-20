# ERA5 cache management design notes

This temporary design note records the follow-up scope after persistent download jobs. It will be replaced by user-facing documentation in the cache-management pull request.

- list content-addressed plans without exposing absolute paths;
- summarize period, domain, coverage, size, age, provenance and dependent download jobs;
- block deletion while any dependent download job is active;
- require an exact plan-key and dependency snapshot confirmation before deletion;
- reject symlinked or non-canonical plan directories;
- preserve a minimal deletion audit outside the deleted plan directory;
- expose the workflow through a local-only API and browser component.
