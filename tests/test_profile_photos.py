import io

import pytest
from PIL import Image

from app.db import connect
from app.services import profile_photos
from app.services.accounts import ValidationError

from .test_auth import PROF_SITE, give_signature, login


def picture(fmt="PNG", size=(800, 400), **options):
    output = io.BytesIO()
    Image.new("RGB", size, "#208d77").save(output, format=fmt, **options)
    output.seek(0)
    return output


@pytest.mark.parametrize("role", ["student", "instructor", "admin"])
def test_picture_upload_replace_remove_for_each_role(app, client, admin_account, role):
    if role == "student":
        give_signature(app, 2)
        uid, site = 2, "http://localhost"
        login(client, "2026-0001", "student123", site)
    elif role == "instructor":
        uid, site = 1, PROF_SITE
        login(client, "prof", "prof123", site)
    else:
        uid, site = admin_account["id"], PROF_SITE
        login(client, admin_account["username"], admin_account["password"], site)
    with connect(app.config["DATABASE"]) as db:
        before = dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    response = client.post("/account/photo", base_url=site,
                           data={"photo": (picture(), "../../someone-else.jpg"), "user_id": "3", "role": "admin"})
    assert response.status_code == 302
    with connect(app.config["DATABASE"]) as db:
        assert dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()) == before
        assert db.execute("SELECT user_id FROM profile_photos").fetchall()[0][0] == uid
    response = client.get("/account/photo", base_url=site)
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.cache_control.private
    with Image.open(io.BytesIO(response.data)) as image:
        assert image.size == (384, 384) and not image.getexif()
    etag = response.headers["ETag"]
    assert client.get("/account/photo", base_url=site, headers={"If-None-Match": etag}).status_code == 304
    download = client.get("/account/photo?download=1", base_url=site)
    assert download.status_code == 200 and download.data == response.data
    assert 'attachment; filename=profile-picture.jpg' == download.headers['Content-Disposition']
    assert b'Your profile picture' in client.get("/account/", base_url=site).data
    assert client.post("/account/photo", base_url=site,
                       data={"photo": (picture("WEBP"), "replacement.webp")}).status_code == 302
    assert client.get("/account/photo", base_url=site).headers["ETag"] != etag
    assert client.post("/account/photo/remove", base_url=site).status_code == 302
    assert client.get("/account/photo", base_url=site).status_code == 404
    assert b'Your profile picture' not in client.get("/account/", base_url=site).data


def test_pictures_are_private_and_cannot_target_another_account(app, client):
    give_signature(app, 2)
    give_signature(app, 3)
    login(client, "2026-0001", "student123")
    client.post("/account/photo", data={"photo": (picture(), "photo.png")})
    other = app.test_client()
    login(other, "2026-0002", "student123")
    assert other.get("/account/photo?user_id=2").status_code == 404
    other.post("/account/photo/remove", data={"user_id": "2"})
    assert client.get("/account/photo").status_code == 200
    anonymous = app.test_client()
    assert anonymous.get("/account/photo").status_code == 302
    assert anonymous.post("/account/photo/remove").status_code == 302


@pytest.mark.parametrize("data", [b"<svg onload='alert(1)'/>", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_bad_upload_preserves_existing_picture(app, client, data):
    login(client, "prof", "prof123", PROF_SITE)
    client.post("/account/photo", base_url=PROF_SITE, data={"photo": (picture(), "real.png")})
    before = client.get("/account/photo", base_url=PROF_SITE).data
    response = client.post("/account/photo", base_url=PROF_SITE,
                           data={"photo": (io.BytesIO(data), "fake.png")})
    assert response.status_code == 400 and b"could not be read" in response.data
    assert b'data-open-on-load' in response.data
    assert client.get("/account/photo", base_url=PROF_SITE).data == before


def test_upload_limits_and_missing_picture(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    assert client.post("/account/photo", base_url=PROF_SITE).status_code == 400
    for count, status in [(profile_photos.MAX_BYTES + 1, 400), (app.config["MAX_CONTENT_LENGTH"] + 1, 413)]:
        response = client.post("/account/photo", base_url=PROF_SITE,
                               data={"photo": (io.BytesIO(b"x" * count), "large.jpg")})
        assert response.status_code == status and b"smaller than 2 MB" in response.data
    with pytest.raises(ValidationError, match="12 megapixels"):
        with connect(app.config["DATABASE"]) as db:
            profile_photos.save(db, 1, picture(size=(4000, 3001)))


def test_metadata_removed_and_phone_rotation_applied(conn):
    exif = Image.Exif()
    exif[274] = 6  # Phone portrait orientation: rotate the stored landscape pixels.
    exif[270] = "Private metadata"
    output = io.BytesIO()
    source = Image.new("RGB", (200, 100), "red")
    source.paste("blue", (100, 0, 200, 100))
    source.save(output, "JPEG", exif=exif)
    output.seek(0)
    profile_photos.save(conn, 1, output)
    raw = conn.execute("SELECT image FROM profile_photos WHERE user_id=1").fetchone()[0]
    with Image.open(io.BytesIO(raw)) as image:
        assert not image.getexif()
        assert image.getpixel((192, 20))[0] > 200   # Red half rotated to the top.
        assert image.getpixel((192, 364))[2] > 200  # Blue half rotated to the bottom.
    assert b"Private metadata" not in raw


def test_picture_changes_require_csrf(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    app.config["CSRF_ENABLED"] = True
    assert client.post("/account/photo", base_url=PROF_SITE,
                       data={"photo": (picture(), "photo.png")}).status_code == 400
    assert client.post("/account/photo/remove", base_url=PROF_SITE).status_code == 400
    with client.session_transaction(base_url=PROF_SITE) as session:
        session["_csrf"] = "valid-token"
    assert client.post("/account/photo", base_url=PROF_SITE,
                       data={"photo": (picture(), "photo.png"), "csrf_token": "valid-token"}).status_code == 302


def test_upload_saves_the_chosen_crop_instead_of_the_center(app, client):
    login(client, "prof", "prof123", PROF_SITE)
    source = Image.new("RGB", (400, 200), "red")
    source.paste("blue", (200, 0, 400, 200))
    output = io.BytesIO()
    source.save(output, "PNG")
    output.seek(0)
    response = client.post("/account/photo", base_url=PROF_SITE, data={
        "photo": (output, "crop.png"), "crop_x": ".5", "crop_y": "0", "crop_size": "1",
    })
    assert response.status_code == 302
    with Image.open(io.BytesIO(client.get("/account/photo", base_url=PROF_SITE).data)) as image:
        for point in ((8, 8), (192, 192), (375, 375)):
            assert image.getpixel(point)[2] > 240 and image.getpixel(point)[0] < 10


def test_zoomed_crop_uses_the_phone_orientation(conn):
    exif = Image.Exif()
    exif[274] = 6
    source = Image.new("RGB", (400, 200), "red")
    source.paste("blue", (200, 0, 400, 200))
    output = io.BytesIO()
    source.save(output, "JPEG", exif=exif)
    output.seek(0)
    profile_photos.save(conn, 1, output, {"crop_x": ".25", "crop_y": ".625", "crop_size": ".5"})
    raw = conn.execute("SELECT image FROM profile_photos WHERE user_id=1").fetchone()[0]
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (384, 384)
        assert image.getpixel((0, 0))[2] > 240
        assert image.getpixel((383, 383))[2] > 240


@pytest.mark.parametrize("change", [
    {"crop_x": "-0.1"}, {"crop_y": "1"}, {"crop_size": "0"}, {"crop_size": "2"},
    {"crop_x": "nan"}, {"crop_size": "inf"}, {"crop_y": "abc"}, {"crop_size": ".00001"},
])
def test_invalid_crop_preserves_picture(conn, change):
    profile_photos.save(conn, 1, picture())
    before = conn.execute("SELECT revision FROM profile_photos WHERE user_id=1").fetchone()[0]
    crop = {"crop_x": "0", "crop_y": "0", "crop_size": "1", **change}
    with pytest.raises(ValidationError, match="adjust its crop"):
        profile_photos.save(conn, 1, picture(), crop)
    assert conn.execute("SELECT revision FROM profile_photos WHERE user_id=1").fetchone()[0] == before


def test_incomplete_crop_is_rejected(conn):
    with pytest.raises(ValidationError, match="adjust its crop"):
        profile_photos.save(conn, 1, picture(), {"crop_x": "0"})
