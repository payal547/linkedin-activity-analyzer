import csv
import io
import re
import zipfile
from datetime import date
from typing import List, Dict, Any, Optional, Tuple
from models.friend import Friend
from models.activity import Activity


def safe_normalize_date(d_val: Optional[str]) -> Optional[str]:
    """Converts any valid date format (ISO, US MM/DD/YYYY, EU DD/MM/YYYY, Month names) to YYYY-MM-DD safely."""
    if not d_val:
        return None
    d_clean = str(d_val).strip()

    # 1. ISO format YYYY-MM-DD (also supports YYYY/MM/DD and YYYY.MM.DD)
    m_iso = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', d_clean)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).isoformat()
        except Exception:
            pass

    # 2. Slash/Dash/Dot format MM/DD/YYYY or DD/MM/YYYY
    m_slash = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', d_clean)
    if m_slash:
        p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        # Unambiguous DD/MM/YYYY (first number > 12, second <= 12)
        if p1 > 12 >= p2:
            try:
                return date(yr, p2, p1).isoformat()
            except Exception:
                pass
        # Unambiguous MM/DD/YYYY (second number > 12, first <= 12)
        elif p2 > 12 >= p1:
            try:
                return date(yr, p1, p2).isoformat()
            except Exception:
                pass
        # Ambiguous MM/DD/YYYY vs DD/MM/YYYY (both <= 12)
        # Default to standard LinkedIn export US format MM/DD/YYYY; if invalid day for month, fallback to DD/MM/YYYY
        elif p1 <= 12 and p2 <= 12:
            try:
                return date(yr, p1, p2).isoformat()
            except Exception:
                try:
                    return date(yr, p2, p1).isoformat()
                except Exception:
                    pass

    # 3. Month name formats
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    m_mon1 = re.search(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})', d_clean, re.IGNORECASE)
    if m_mon1:
        day, mon_str, yr = int(m_mon1.group(1)), m_mon1.group(2).lower()[:3], int(m_mon1.group(3))
        if mon_str in months:
            try:
                return date(yr, months.index(mon_str) + 1, day).isoformat()
            except Exception:
                pass

    m_mon2 = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', d_clean, re.IGNORECASE)
    if m_mon2:
        mon_str, day, yr = m_mon2.group(1).lower()[:3], int(m_mon2.group(2)), int(m_mon2.group(3))
        if mon_str in months:
            try:
                return date(yr, months.index(mon_str) + 1, day).isoformat()
            except Exception:
                pass

    return None


def validate_linkedin_connections_csv(csv_text: str) -> Tuple[bool, str, int]:
    """
    Validates that an uploaded CSV is an official LinkedIn Connections export.
    Returns (is_valid, message, header_line_index).
    """
    if not csv_text or not csv_text.strip():
        return False, "The uploaded file is empty.", -1

    # Use csv.reader to safely parse lines without tripping over multiline notes/disclaimers
    stream = io.StringIO(csv_text.strip())
    reader = csv.reader(stream)
    header_idx = -1
    for idx, row in enumerate(reader):
        if idx > 30:
            break
        lower_cols = [c.strip().lower() for c in row if c]
        if any("first name" in c for c in lower_cols) and any("last name" in c for c in lower_cols):
            header_idx = idx
            break

    if header_idx == -1:
        return False, "Missing 'First Name' and 'Last Name' columns. Ensure this is an official LinkedIn Connections.csv export.", -1

    return True, "Valid LinkedIn Connections export.", header_idx


def parse_connections_csv(csv_text: str) -> List[Friend]:
    """
    Parses an official LinkedIn Connections.csv export (contains: First Name, Last Name, URL, Company, Position).
    Safely ignores sensitive fields (e.g. Email Address) and handles missing columns.
    Checks for legitimate profile photo/picture URL if present in columns.
    Reads ALL valid rows without any arbitrary limit.
    Returns list of Friend model objects labeled as 'imported'.
    """
    is_valid, _, header_index = validate_linkedin_connections_csv(csv_text)
    if not is_valid or header_index < 0:
        return []

    stream = io.StringIO(csv_text.strip())
    reader = csv.reader(stream)
    header = None
    for idx, row in enumerate(reader):
        if idx == header_index:
            header = [c.strip() for c in row]
            break

    if not header:
        return []

    dict_reader = csv.DictReader(stream, fieldnames=header)
    connections = []

    for idx, row in enumerate(dict_reader):
        first_name = (row.get("First Name") or row.get("first_name") or row.get("FirstName") or "").strip()
        last_name = (row.get("Last Name") or row.get("last_name") or row.get("LastName") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            continue

        company = (row.get("Company") or row.get("company") or "").strip()
        position = (row.get("Position") or row.get("position") or "").strip()
        if position and company:
            headline = f"{position} at {company}"
        elif position:
            headline = position
        elif company:
            headline = f"Member at {company}"
        else:
            headline = "LinkedIn Connection"

        profile_url = (row.get("URL") or row.get("url") or row.get("Profile URL") or "").strip()
        connected_on = (row.get("Connected On") or row.get("connected_on") or row.get("Connected on") or "").strip()

        # Check for legitimate photo/picture URL in row if present in the data
        picture_url = None
        for key, val in row.items():
            if key and any(pk in key.lower() for pk in ["picture", "photo", "avatar", "image"]):
                cleaned_val = (val or "").strip()
                if cleaned_val.startswith(("http://", "https://")):
                    picture_url = cleaned_val
                    break

        connections.append(Friend(
            id=f"conn_imp_{idx+1}",
            name=full_name,
            headline=headline,
            company=company or None,
            position=position or None,
            picture=picture_url,
            profile_url=profile_url or None,
            connected_on=connected_on or None,
            source="imported"
        ))

    return connections


def validate_linkedin_invitations_csv(csv_text: str) -> Tuple[bool, str, int]:
    """Validates that a CSV is a LinkedIn Invitations export (From, To, Sent At, Direction, etc.)."""
    if not csv_text or not csv_text.strip():
        return False, "File is empty.", -1

    stream = io.StringIO(csv_text.strip())
    reader = csv.reader(stream)
    for idx, row in enumerate(reader):
        if idx > 15:
            break
        lower = [c.strip().lower() for c in row if c]
        if "direction" in lower or ("from" in lower and "to" in lower):
            return True, "Valid Invitations export.", idx
    return False, "Not an Invitations CSV.", -1


def parse_invitations_csv(csv_text: str, existing_names: Optional[set] = None) -> List[Friend]:
    """
    Parses LinkedIn's Invitations.csv to retrieve network contacts.
    Deduplicates against already imported connections.
    """
    is_valid, _, header_index = validate_linkedin_invitations_csv(csv_text)
    if not is_valid or header_index < 0:
        return []

    stream = io.StringIO(csv_text.strip())
    reader = csv.reader(stream)
    header = None
    for idx, row in enumerate(reader):
        if idx == header_index:
            header = [c.strip() for c in row]
            break

    if not header:
        return []

    dict_reader = csv.DictReader(stream, fieldnames=header)
    seen = set(existing_names or set())
    invitations = []

    for idx, row in enumerate(dict_reader):
        direction = (row.get("Direction") or row.get("direction") or "").strip().upper()
        if direction == "OUTGOING":
            contact_name = (row.get("To") or row.get("to") or "").strip()
            profile_url = (row.get("inviteeProfileUrl") or row.get("inviteeprofileurl") or "").strip()
            headline = "LinkedIn Contact (Invitation Sent)"
        else:
            contact_name = (row.get("From") or row.get("from") or "").strip()
            profile_url = (row.get("inviterProfileUrl") or row.get("inviterprofileurl") or "").strip()
            headline = "LinkedIn Contact (Invitation Received)"

        if not contact_name or contact_name.lower() in seen:
            continue

        sent_at = (row.get("Sent At") or row.get("sent at") or row.get("Date") or "").strip()
        seen.add(contact_name.lower())

        invitations.append(Friend(
            id=f"conn_inv_{idx+1}",
            name=contact_name,
            headline=headline,
            company=None,
            position=None,
            picture=None,
            profile_url=profile_url or None,
            connected_on=sent_at or None,
            source="imported"
        ))

    return invitations


def parse_connections_from_file_or_zip(file_bytes: bytes, filename: str) -> Tuple[bool, List[Friend], str]:
    """
    Extract and parse all connections from a CSV file or full LinkedIn export ZIP.
    Reads the complete Connections.csv without any artificial limits.
    If inside a ZIP archive, also parses Invitations.csv for any additional network contacts.
    """
    if not file_bytes:
        return False, [], "The uploaded file is empty."

    # Check if file is a ZIP archive
    if filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(file_bytes)):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                # Check ZIP integrity before parsing
                corrupt_file = zf.testzip()
                if corrupt_file:
                    return False, [], f"Corrupted file '{corrupt_file}' encountered in ZIP archive."

                conn_friends = []
                inv_friends = []

                # 1. Look for Connections.csv
                for name in zf.namelist():
                    if ".." in name or name.startswith(("/", "\\")):
                        continue  # Path traversal protection
                    if "connections.csv" in name.lower():
                        csv_text = zf.read(name).decode("utf-8-sig", errors="replace")
                        is_valid, msg, _ = validate_linkedin_connections_csv(csv_text)
                        if is_valid:
                            conn_friends = parse_connections_csv(csv_text)
                        break

                # 2. Look for Invitations.csv to include any pending/network contacts
                existing_names = {f.name.lower() for f in conn_friends}
                for name in zf.namelist():
                    if ".." in name or name.startswith(("/", "\\")):
                        continue  # Path traversal protection
                    if "invitations.csv" in name.lower():
                        csv_text = zf.read(name).decode("utf-8-sig", errors="replace")
                        is_valid, _, _ = validate_linkedin_invitations_csv(csv_text)
                        if is_valid:
                            inv_friends = parse_invitations_csv(csv_text, existing_names)
                        break

                all_friends = conn_friends + inv_friends
                if not all_friends:
                    return False, [], "No connection or invitation records found inside the uploaded ZIP archive."

                msg = f"Successfully loaded {len(all_friends)} real connections from your LinkedIn export."
                if conn_friends and inv_friends:
                    msg = f"Successfully loaded {len(all_friends)} connections ({len(conn_friends)} accepted connections from Connections.csv and {len(inv_friends)} contacts from Invitations.csv)."
                elif conn_friends:
                    msg = f"Successfully loaded {len(conn_friends)} accepted connections from Connections.csv."

                return True, all_friends, msg
        except Exception as e:
            return False, [], f"Failed to extract ZIP archive: {str(e)}"

    # Otherwise treat as CSV
    try:
        csv_text = file_bytes.decode("utf-8-sig", errors="replace")
        is_valid, msg, _ = validate_linkedin_connections_csv(csv_text)
        if is_valid:
            friends = parse_connections_csv(csv_text)
            if not friends:
                return False, [], "No valid connection rows found in Connections.csv."
            return True, friends, f"Successfully loaded all {len(friends)} connection(s) from Connections.csv."

        # If not standard Connections.csv, check if user uploaded Invitations.csv directly
        is_inv_valid, _, _ = validate_linkedin_invitations_csv(csv_text)
        if is_inv_valid:
            friends = parse_invitations_csv(csv_text)
            if friends:
                return True, friends, f"Successfully loaded {len(friends)} contact(s) from Invitations.csv."

        return False, [], msg
    except Exception as e:
        return False, [], f"Failed to process CSV file: {str(e)}"


def parse_reactions_csv(csv_text: str) -> List[Activity]:
    """
    Parses LinkedIn's Reactions.csv (when you/friend liked or reacted to someone's post).
    Columns: Date, Type, Link
    """
    lines = csv_text.strip().splitlines()
    start_index = 0
    for idx, line in enumerate(lines[:10]):
        if "Date" in line or "date" in line.lower():
            start_index = idx
            break

    cleaned_content = "\n".join(lines[start_index:])
    reader = csv.DictReader(io.StringIO(cleaned_content))
    activities = []

    for idx, row in enumerate(reader):
        raw_d = (row.get("Date") or row.get("date") or "").strip()
        date_str = safe_normalize_date(raw_d) or raw_d[:10]
        reaction_type = (row.get("Type") or row.get("type") or "LIKE").strip()
        link = (row.get("Link") or row.get("link") or "").strip()
        clean_link = link.split('?')[0].split('#')[0] if link else None
        if not date_str:
            continue

        rx_title = reaction_type.title()
        activities.append(Activity(
            id=f"react_{idx+1}",
            date=date_str,
            type="like",
            text=f"Liked/Reacted ({rx_title}) to LinkedIn post: {clean_link or link}" if link else f"Liked post ({rx_title})",
            likes=1,
            comments=0,
            shares=0,
            source="imported",
            post_content=f"LinkedIn post: {clean_link or link}" if link else "LinkedIn post",
            post_author="Original Post Author",
            post_url=clean_link or None,
            reaction_type=rx_title,
            is_own_post=False,
            action_type="like"
        ))

    return activities


def parse_comments_csv(csv_text: str) -> List[Activity]:
    """
    Parses LinkedIn's Comments.csv (when you/friend commented on someone's post).
    Columns: Date, Link, Message
    """
    lines = csv_text.strip().splitlines()
    start_index = 0
    for idx, line in enumerate(lines[:10]):
        if "Date" in line or "date" in line.lower():
            start_index = idx
            break

    cleaned_content = "\n".join(lines[start_index:])
    reader = csv.DictReader(io.StringIO(cleaned_content))
    activities = []

    for idx, row in enumerate(reader):
        raw_d = (row.get("Date") or row.get("date") or "").strip()
        date_str = safe_normalize_date(raw_d) or raw_d[:10]
        message = (row.get("Message") or row.get("message") or "").strip()
        link = (row.get("Link") or row.get("link") or "").strip()
        clean_link = link.split('?')[0].split('#')[0] if link else None
        if not date_str:
            continue

        activities.append(Activity(
            id=f"comm_{idx+1}",
            date=date_str,
            type="comment",
            text=message or f"Commented on LinkedIn post: {clean_link or link}",
            likes=0,
            comments=1,
            shares=0,
            source="imported",
            post_content=f"LinkedIn post: {clean_link or link}" if link else "LinkedIn post",
            post_author="Original Post Author",
            post_url=clean_link or None,
            comment_text=message or None,
            is_own_post=False,
            action_type="comment"
        ))

    return activities


def parse_shares_csv(csv_text: str) -> List[Activity]:
    """
    Parses LinkedIn's Shares.csv / Posts.csv (original posts, shares, videos published).
    Columns: Date, ShareCommentary, SharedUrl, MediaUrl, Visibility
    """
    lines = csv_text.strip().splitlines()
    start_index = 0
    for idx, line in enumerate(lines[:10]):
        if "Date" in line or "date" in line.lower():
            start_index = idx
            break

    cleaned_content = "\n".join(lines[start_index:])
    reader = csv.DictReader(io.StringIO(cleaned_content))
    activities = []

    for idx, row in enumerate(reader):
        raw_d = (row.get("Date") or row.get("date") or "").strip()
        date_str = safe_normalize_date(raw_d) or raw_d[:10]
        commentary = (row.get("ShareCommentary") or row.get("sharecommentary") or row.get("text") or "").strip()
        url = (row.get("SharedUrl") or row.get("sharedurl") or row.get("ShareLink") or row.get("sharelink") or row.get("Link") or row.get("link") or "").strip()
        media_url = (row.get("MediaUrl") or row.get("mediaurl") or "").strip()
        if not date_str:
            continue

        clean_url = url.split('?')[0].split('#')[0] if url else None
        clean_media = media_url.split('?')[0] if media_url else None

        # Video detection: check genuine video URL extensions / video hosts without false-positiving on commentary words or generic post URNs
        is_video = bool(
            (clean_media and any(ext in clean_media.lower() for ext in (".mp4", ".mov", ".webm", ".m4v", "videocdn", "/video/", "dms/video/", "urn:li:video:"))) or
            (clean_url and (
                any(v_host in clean_url.lower() for v_host in ("youtube.com/watch", "youtu.be/", "vimeo.com/", "linkedin.com/video/")) or
                "urn:li:video:" in clean_url.lower()
            ))
        )

        # Share / Repost detection: commentary explicitly starting with Reposted/Shared or sharing someone else's post
        is_share = (
            not is_video and (
                commentary.lower().startswith(("reposted", "shared")) or
                ("reposted this" in commentary.lower()) or
                (clean_url and "linkedin.com/posts/" in clean_url.lower() and not commentary)
            )
        )

        if is_video:
            act_type = "video"
        elif is_share:
            act_type = "share"
        else:
            act_type = "post"

        v_url = clean_media if (clean_media and any(ext in clean_media.lower() for ext in (".mp4", ".mov", ".webm", ".m4v", "videocdn", "/video/", "dms/video/"))) else None
        if not v_url and clean_url and any(v_host in clean_url.lower() for v_host in ("youtube.com", "youtu.be", "vimeo.com")):
            v_url = clean_url
        v_status = "Video stream extracted" if v_url else ("Video detected in export" if is_video else "No video")

        activities.append(Activity(
            id=f"share_{idx+1}",
            date=date_str,
            type=act_type,
            text=commentary or clean_url or "Published update on LinkedIn",
            likes=0,
            comments=0,
            shares=0,
            source="imported",
            post_content=commentary or clean_url or "Published update on LinkedIn",
            post_author=None,
            post_url=clean_url or None,
            media_url=clean_media or None,
            video_url=v_url,
            video_thumbnail=None,
            video_status=v_status,
            is_own_post=True,
            action_type=act_type
        ))

    return activities


def parse_activities_csv(csv_text: str) -> List[Activity]:
    """
    Parses a general activity CSV (date, type, text, likes, comments, shares),
    or intelligently routes to specialized LinkedIn export parsers.
    """
    sample = csv_text[:1500].lower()
    if "sharecommentary" in sample:
        return parse_shares_csv(csv_text)
    if "message" in sample and "link" in sample:
        return parse_comments_csv(csv_text)
    if "link" in sample and "type" in sample and "likes" not in sample:
        return parse_reactions_csv(csv_text)

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return []

    normalized_fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    date_col = normalized_fields.get("date")
    type_col = normalized_fields.get("type")

    if not date_col or not type_col:
        return []

    activities = []
    for idx, row in enumerate(reader):
        raw_date_str = (row.get(date_col) or "").strip()
        norm_date = safe_normalize_date(raw_date_str) or raw_date_str[:10]
        raw_type = (row.get(type_col) or "").strip().lower()
        if not norm_date or not raw_type:
            continue

        raw_type = raw_type[:-1] if raw_type.endswith("s") else raw_type

        def safe_int(val):
            try:
                return max(0, int(float(val or 0)))
            except (ValueError, TypeError):
                return 0

        act_text = (row.get(normalized_fields.get("text", "text")) or "").strip()
        p_content = (row.get(normalized_fields.get("post_content", "post_content")) or act_text).strip()
        raw_p_url = (row.get(normalized_fields.get("post_url", "post_url")) or row.get(normalized_fields.get("link", "link")) or "").strip()
        p_url = raw_p_url.split('?')[0].split('#')[0] if raw_p_url else None
        p_author = (row.get(normalized_fields.get("post_author", "post_author")) or "").strip()
        c_text = (row.get(normalized_fields.get("comment_text", "comment_text")) or "").strip()
        r_type = (row.get(normalized_fields.get("reaction_type", "reaction_type")) or "").strip()

        activities.append(Activity(
            id=f"imported_act_{idx+1}",
            date=norm_date,
            type=raw_type,
            text=act_text,
            likes=safe_int(row.get(normalized_fields.get("likes", "likes"))),
            comments=safe_int(row.get(normalized_fields.get("comments", "comments"))),
            shares=safe_int(row.get(normalized_fields.get("shares", "shares"))),
            source="imported",
            post_content=p_content or act_text,
            post_author=p_author or None,
            post_url=p_url or None,
            comment_text=c_text or None,
            reaction_type=r_type or None,
            is_own_post=(raw_type in ("post", "video", "share")),
            action_type=raw_type
        ))

    return activities


def parse_activities_from_file_or_zip(file_bytes: bytes, filename: str) -> List[Activity]:
    """Parses activities from any official LinkedIn CSV or complete export ZIP with cross-file deduplication."""
    if filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(file_bytes)):
        all_activities = []
        seen_keys = set()
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                # Check ZIP integrity before parsing
                corrupt_file = zf.testzip()
                if corrupt_file:
                    return []

                for name in zf.namelist():
                    if ".." in name or name.startswith(("/", "\\")):
                        continue  # Path traversal protection
                    lower = name.lower()
                    file_acts: List[Activity] = []
                    if "reactions.csv" in lower:
                        txt = zf.read(name).decode("utf-8-sig", errors="replace")
                        file_acts = parse_reactions_csv(txt)
                    elif "comments.csv" in lower:
                        txt = zf.read(name).decode("utf-8-sig", errors="replace")
                        file_acts = parse_comments_csv(txt)
                    elif "shares.csv" in lower or "posts.csv" in lower:
                        txt = zf.read(name).decode("utf-8-sig", errors="replace")
                        file_acts = parse_shares_csv(txt)
                    elif "activities.csv" in lower:
                        txt = zf.read(name).decode("utf-8-sig", errors="replace")
                        file_acts = parse_activities_csv(txt)

                    for act in file_acts:
                        p_url = (act.post_url or "").split('?')[0].split('#')[0]
                        dedup_key = f"{act.type}_{act.date}_{p_url or (act.text or '')[:40]}"
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            all_activities.append(act)
        except Exception:
            return all_activities
        return all_activities

    csv_text = file_bytes.decode("utf-8-sig", errors="replace")
    lower = filename.lower()
    if "reaction" in lower:
        return parse_reactions_csv(csv_text)
    if "comment" in lower:
        return parse_comments_csv(csv_text)
    if "share" in lower or "post" in lower:
        return parse_shares_csv(csv_text)
    return parse_activities_csv(csv_text)

