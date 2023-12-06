import json
from html.parser import HTMLParser
from http_router import Router
from jinja2 import Environment, FileSystemLoader, select_autoescape
from spin_http import Response, Request, http_send
from spin_key_value import kv_open_default
from spin_llm import LLMInferencingParams, llm_infer
from urllib.parse import urlparse, parse_qs

# Create the new router
router = Router(trim_last_slash=True)


# Add a new bookmark
@router.route("/add", methods=["POST"])
def add_url(request):
    # This gets us the encoded form data
    params = parse_qs(request.body, keep_blank_values=True)
    title = params[b"title"][0].decode()
    url = params[b"url"][0].decode()

    # Open key value storage
    store = kv_open_default()

    # Get the existing bookmarks or initialize an empty bookmark list
    bookmark_entry = store.get("bookmarks") or b"[]"
    bookmarks = json.loads(bookmark_entry)

    # Generate a page summary
    summary_text = summarize_page(url)

    # Add our new entry.
    bookmarks.append({"title": title, "url": url, "summary": summary_text})

    # Store the modified list in key value store
    new_bookmarks = json.dumps(bookmarks)
    store.set("bookmarks", bytes(new_bookmarks, "utf-8"))

    # Direct the client to go back to the index.html
    return Response(303, {"location": "/index.html"})


# This is our index handler
@router.route("/", "/index.html")
def index(request):
    # Set up Jinja2 to load anything we put in "/".
    # This only has access to the files mapped in spin.toml.
    env = Environment(loader=FileSystemLoader("/"), autoescape=select_autoescape())

    # Get our bookmarks out of KV store and parse the JSON
    store = kv_open_default()
    bookmarks = json.loads(store.get("bookmarks") or b"[]")

    # Render the template
    template = env.get_template("index.html")
    buf = template.render(title="Bookmarker", bookmarks=bookmarks)

    # REMOVE ME!
    # This resets the page after each refresh.
    # reset(request)

    return Response(
        200,
        {"content-type": "text/html"},
        bytes(buf, "utf-8"),
    )


@router.route("/reset")
def reset(request):
    store = kv_open_default()
    store.delete("bookmarks")
    return Response(200, {"content-type": "text/plain"}, b"Storage has been reset")


# Main Spin entrypoint
def handle_request(request):
    # Look up the route for the given URI and get a handler.
    uri = urlparse(request.uri)
    handler = router(uri.path, request.method)

    # Now that we have a handler, let's call the function.
    # For `/` and `/index.html`, this is calling index().
    return handler.target(request)


def summarize_page(url):
    print(url)
    req = Request("GET", url, {}, None)
    res = http_send(req)
    match res.status:
        # This is to support Spin runtimes that don't automatically
        # follow redirects. For Spin itself, it works fine without
        # this case.
        case 301 | 303 | 304 | 307:
            loc = res.headers["location"]
            print(f"following redirect to {loc}")
            return summarize_page(loc)
        case 200:
            return summarize(res.body.decode("utf-8"))
        case _:
            return "Unable to load preview"


class HTMLTitleParser(HTMLParser):
    track_title = False
    track_article = False
    title_data = ""
    article_data = ""

    def get_content(self):
        return f"{self.title_data}\n{self.article_data}"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self.track_article = True
        if tag == "title":
            self.track_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            self.track_article = False
        if tag == "title":
            self.track_title = False

    def handle_data(self, data: str) -> None:
        if self.track_title:
            self.title_data += data
        if self.track_article:
            self.article_data += data


# Summarize an HTML document
def summarize(doc):
    parser = HTMLTitleParser()
    parser.feed(doc)
    text = parser.get_content()
    # Now we have the HTML body. Let's see if the LLM can handle this:
    prompt = f"""<s><<SYS>>You are an academic text summarizer. Your style is concise and minimal. Succinctly summarize the article.<</SYS>>
[[INST]]{text}[[/INST]]
"""
    print(prompt)
    opts = LLMInferencingParams(50000, 1.1, 64, 0.8, 40, 0.9)
    return llm_infer("llama2-chat", prompt, options=opts).text
