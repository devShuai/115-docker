# box64 settings for a Chromium-based browser. box64's own rc file applies these to
# "chrome" and Steam's Chromium helper by program name, which 115Browser never matches.
# Chromium replaces malloc/free with its own allocator; without MALLOC_HACK=2 the
# browser aborts at once with "free(): invalid pointer".
export BOX64_NOBANNER=1 BOX64_SHOWSEGV=1 BOX64_MALLOC_HACK=2
export BOX64_DYNAREC_STRONGMEM=1 BOX64_DYNAREC_BIGBLOCK=3 BOX64_DYNAREC_CALLRET=1
export BOX64_DYNAREC_FASTROUND=0 BOX64_DYNAREC_SAFEFLAGS=0
