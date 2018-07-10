import logging
import traceback
import urllib.parse

import flask
import sqlalchemy
from flask_oauthlib.client import OAuth, OAuthException

from .. import app, config, model, util
from ..util import cross_origin


login_log = logging.getLogger("login")


oauth_login = flask.Blueprint("github_login", __name__)
oauth_logout = flask.Blueprint("oauth_logout", __name__)

oauth = OAuth(app)
github = oauth.remote_app(
    "github",
    consumer_key=config.OAUTH_GITHUB_CONSUMER_KEY,
    consumer_secret=config.OAUTH_GITHUB_CONSUMER_SECRET,
    request_token_params={"scope": "user:email"},
    base_url="https://api.github.com",
    request_token_url=None,
    access_token_method="POST",
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
)
google = oauth.remote_app(
    "google",
    consumer_key=config.OAUTH_GOOGLE_CONSUMER_KEY,
    consumer_secret=config.OAUTH_GOOGLE_CONSUMER_SECRET,
    request_token_params={'scope': 'email'},
    base_url='https://www.googleapis.com/oauth2/v1/',
    request_token_url=None,
    access_token_method='POST',
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
)


@oauth_login.route("/github")
def github_login_init():
    url = urllib.parse.urlparse(config.API_URL)
    base_url = url.scheme + "://" + url.netloc
    full_url = urllib.parse.urljoin(
        base_url,
        flask.url_for(".github_login_callback"))
    return github.authorize(callback=full_url)


@oauth_login.route("/google")
def google_login_init():
    url = urllib.parse.urlparse(config.API_URL)
    base_url = url.scheme + "://" + url.netloc
    full_url = urllib.parse.urljoin(
        base_url,
        flask.url_for(".google_login_callback"))
    return google.authorize(callback=full_url)


@oauth_login.route("/me")
@cross_origin(methods=["GET"], origins=config.CORS_ORIGINS, supports_credentials=True)
def me():
    if "user_id" in flask.session:
        return flask.jsonify({
            "user_id": flask.session["user_id"],
        })
    else:
        return flask.jsonify(None)


@oauth_logout.route("/", methods=["POST"])
@cross_origin(methods=["POST"], origins=config.CORS_ORIGINS, supports_credentials=True)
def logout():
    flask.session.clear()
    return util.response_success()


@oauth_login.route("/response/github")
def github_login_callback():
    try:
        response = github.authorized_response()
    except OAuthException:
        login_log.error(traceback.format_exc())
        raise

    if response is None or not response.get("access_token"):
        if response and "error" in response:
            login_log.error("Got OAuth error: {}".format(response))

            raise util.APIError(
                403,
                message="Access denied. Reason: {error}. Error: {error_description}".format(**response)
            )

        raise util.APIError(
            403,
            message="Access denied. Reason: {}. Error: {}.".format(
                flask.request.args["error"],
                flask.request.args["error_description"],
            )
        )

    flask.session["github_token"] = (response["access_token"], "")

    user_data = github.get("user").data

    username = user_data["login"]
    github_user_id = user_data["id"]
    emails = github.get("user/emails").data

    email = emails[0]["email"]
    for record in emails:
        if record["primary"]:
            email = record["email"]
            break

    return generic_login_callback(username, email, 1, github_user_id)


@oauth_login.route("/response/google")
def google_login_callback():
    try:
        response = google.authorized_response()
    except OAuthException:
        login_log.error(traceback.format_exc())
        raise

    if response is None or not response.get("access_token"):
        if response and "error" in response:
            login_log.error("Got OAuth error: {}".format(response))

            raise util.APIError(
                403,
                message="Access denied. Reason: {error}. Error: {error_description}".format(**response)
            )

        raise util.APIError(
            403,
            message="Access denied. Reason: {}. Error: {}.".format(
                flask.request.args["error"],
                flask.request.args["error_description"],
            )
        )

    flask.session["google_token"] = (response["access_token"], "")

    user_data = google.get("userinfo").data

    # TODO: better way of doing this?
    # TODO: username collisions between services...
    username = user_data["email"].split("@")[0]
    google_user_id = user_data["id"]
    email = user_data["email"]
    # TODO: factor this out into constant
    return generic_login_callback(username, email, 2, google_user_id)


def generic_login_callback(username, email, oauth_provider, oauth_id):
    with model.engine.connect() as conn:
        user = conn.execute(sqlalchemy.sql.select([
            model.users.c.id,
        ]).select_from(model.users).where(
            (model.users.c.oauth_provider == oauth_provider) &
            (model.users.c.oauth_id == oauth_id)
        )).first()

        if not user:
            # New user
            new_user_id = conn.execute(model.users.insert().values(
                username=username,
                # TODO: rename github_email to oauth_email
                github_email=email,
                oauth_id=oauth_id,
                oauth_provider=oauth_provider,
            )).inserted_primary_key
            flask.session["user_id"] = new_user_id[0]
            return flask.redirect(urllib.parse.urljoin(config.SITE_URL, "/create-account"))
        else:
            flask.session["user_id"] = user["id"]
            return flask.redirect(urllib.parse.urljoin(config.SITE_URL, "/user/?me"))

    if "redirectURL" in flask.request.args:
        return flask.redirect(flask.request.args["redirectURL"])

    return util.response_success()


@github.tokengetter
def github_tokengetter():
    return flask.session.get("github_token")


@google.tokengetter
def google_tokengetter():
    return flask.session.get("google_token")
