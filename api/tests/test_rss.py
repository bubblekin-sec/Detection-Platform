from app.sources.rss import canonical_url, html_to_text

# Modeled on a real page: an early selector (`main`) matches only a teaser, a later one (`.content`) holds the post.
PAGE = """
<html><body>
  <main><p>Read our latest report. Short teaser text.</p></main>
  <div class="content"><p>Initial access was a malicious search result leading to a fake installer.</p>
  <p>The installer ran msiexec /i update.msi /qn and then rundll32 to start Bumblebee.</p></div>
</body></html>
"""


def test_uses_the_first_selector_with_enough_text():
    text = html_to_text(PAGE, ["article", "main", ".content"], min_chars=100)

    assert "msiexec /i update.msi /qn" in text
    assert "teaser" not in text


def test_falls_back_to_the_longest_match_when_none_is_long_enough():
    text = html_to_text(PAGE, ["main", ".content"], min_chars=10_000)

    assert "msiexec" in text


def test_first_matching_selector_wins_when_it_is_long_enough():
    assert "teaser" in html_to_text(PAGE, ["main", ".content"], min_chars=10)


def test_scrapy_pseudo_elements_and_invalid_selectors_are_ignored():
    assert "msiexec" in html_to_text(PAGE, ["h1::text", "[[bad", ".content::attr(x)"], min_chars=50)


def test_without_selectors_uses_article_or_body_and_drops_chrome():
    html = "<html><body><nav>Menu</nav><article><p>Body text</p><script>x()</script></article><footer>Foot</footer></body></html>"

    assert html_to_text(html) == "Body text"


def test_inline_tags_stay_on_their_line_and_blocks_get_their_own():
    html = (
        "<article><h2>Execution</h2><p>The operators ran <code>rundll32.exe javascript:\"\\..\\mshtml\"</code> "
        "to <em>launch</em> a <a href='#'>loader</a>.</p><ul><li>First</li><li>Second<br>line</li></ul></article>"
    )

    assert html_to_text(html).split("\n") == [
        "Execution",
        'The operators ran rundll32.exe javascript:"\\..\\mshtml" to launch a loader.',
        "First",
        "Second",
        "line",
    ]


def test_canonical_url_drops_fragment_and_tracking_but_keeps_real_params():
    assert canonical_url("HTTPS://Blog.Example.com/post?id=7&utm_source=rss&fbclid=x#comments") == "https://blog.example.com/post?id=7"


def test_text_extraction_is_linear_on_huge_flat_pages():
    import time

    html = "<div>" + "<p>x</p><script>y</script>" * 100_000 + "</div>"
    started = time.monotonic()

    text = html_to_text(html)

    # Linear takes ~4 s on a dev laptop; the old quadratic version took minutes. Loose bound avoids load flakes.
    assert time.monotonic() - started < 20
    assert text.count("x") == 100_000 and "y" not in text


def test_canonical_url_keeps_userinfo_case_meaningful_params_and_encoding():
    url = "https://User:PaSS@Example.COM/a%20b?ref=main&q=a%20b&flag&utm_medium=x"

    assert canonical_url(url) == "https://User:PaSS@example.com/a%20b?ref=main&q=a%20b&flag"
