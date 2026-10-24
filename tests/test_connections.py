import unittest
from unittest.mock import patch, MagicMock
import io
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app
from linkedin.data_parser import validate_linkedin_connections_csv, parse_connections_csv


SAMPLE_LINKEDIN_CSV = """Notes:
When exporting your connection data, that member's email address is only included if...

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Aman,Verma,https://www.linkedin.com/in/aman-verma,private.aman@example.com,Microsoft,Software Engineer,12 Oct 2024
Priya,Sharma,https://www.linkedin.com/in/priya-sharma,private.priya@example.com,Google,Product Manager,15 Sep 2024
Rohit,Singh,,,Amazon,Data Analyst,20 Aug 2024
"""


class TestConnections(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        test_file = Path(__file__).resolve().parent.parent / "backend" / "data" / "test_connections.json"
        if test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass

    def test_profile_unauthenticated(self):
        response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 401)
        data = response.get_json()
        self.assertFalse(data.get("connected"))

    def test_connections_unauthenticated(self):
        response = self.client.get("/api/connections")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data.get("connected"))
        self.assertIsNone(data.get("self_profile"))
        self.assertIsInstance(data.get("connections"), list)

    def test_import_connections_unauthenticated(self):
        # Verify offline import works without requiring LinkedIn OAuth login
        data = {
            "file": (io.BytesIO(SAMPLE_LINKEDIN_CSV.encode("utf-8")), "Connections.csv")
        }
        response = self.client.post("/api/connections/import", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        res_data = response.get_json()
        self.assertIn("Successfully loaded", res_data.get("message", ""))
        self.assertGreaterEqual(res_data.get("count", 0), 3)

    def test_profile_authenticated(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test User", "email": "test@example.com"}
            sess["linkedin_session_id"] = "test-session"
            sess["linkedin_access_token"] = "test-token"

        response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("connected"))
        self.assertEqual(data.get("name"), "Test User")

    @patch("routes.connection_routes.load_saved_connections", return_value=[])
    @patch("requests.get")
    def test_connections_api_restricted_graceful_handling(self, mock_get, mock_load):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Not enough permissions to access: GET-viewer /connections"
        mock_get.return_value = mock_resp

        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test User", "email": "test@example.com"}
            sess["linkedin_session_id"] = "test-session"
            sess["linkedin_access_token"] = "test-token"

        response = self.client.get("/api/connections")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("connected"))
        self.assertTrue(data.get("api_restricted"))
        self.assertEqual(data.get("connections"), [])

    def test_csv_parser_validation(self):
        is_valid, msg, header_idx = validate_linkedin_connections_csv(SAMPLE_LINKEDIN_CSV)
        self.assertTrue(is_valid)
        self.assertEqual(header_idx, 3)

        friends = parse_connections_csv(SAMPLE_LINKEDIN_CSV)
        self.assertEqual(len(friends), 3)

        # Check First record
        self.assertEqual(friends[0].name, "Aman Verma")
        self.assertEqual(friends[0].headline, "Software Engineer at Microsoft")
        self.assertEqual(friends[0].company, "Microsoft")
        self.assertEqual(friends[0].position, "Software Engineer")
        self.assertEqual(friends[0].source, "imported")
        self.assertEqual(friends[0].connected_on, "12 Oct 2024")

        # Privacy test: verify Email Address was NOT added to Friend objects
        friend_dict = friends[0].to_dict()
        self.assertNotIn("email", friend_dict)
        self.assertNotIn("Email Address", friend_dict)
        self.assertNotIn("private.aman@example.com", str(friend_dict))

        # Check missing URL handling
        self.assertEqual(friends[2].name, "Rohit Singh")
        self.assertIsNone(friends[2].profile_url)

    def test_csv_parser_rejects_invalid_csv(self):
        invalid_csv = "Item,Price,Quantity\nApple,10,2\nBanana,5,6"
        is_valid, msg, header_idx = validate_linkedin_connections_csv(invalid_csv)
        self.assertFalse(is_valid)
        self.assertIn("Missing 'First Name' and 'Last Name'", msg)

        empty_csv = ""
        is_valid, msg, header_idx = validate_linkedin_connections_csv(empty_csv)
        self.assertFalse(is_valid)

    def test_import_connections_endpoint(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test User", "email": "test@example.com"}

        data = {
            "file": (io.BytesIO(SAMPLE_LINKEDIN_CSV.encode("utf-8")), "Connections.csv")
        }

        response = self.client.post("/api/connections/import", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        res_data = response.get_json()
        self.assertIn("Successfully loaded", res_data.get("message", ""))
        self.assertGreaterEqual(res_data.get("count", 0), 3)

    def test_import_connections_invalid_file(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test User", "email": "test@example.com"}

        data = {
            "file": (io.BytesIO(b"random,unrelated,csv,data\n1,2,3,4"), "sample.csv")
        }

        response = self.client.post("/api/connections/import", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        res_data = response.get_json()
        self.assertIn("error", res_data)

    def test_parse_large_connections_csv_no_limits(self):
        """Verify that CSV with dozens or hundreds of rows parses ALL rows with zero limit."""
        csv_rows = [
            "Notes:",
            "Multi-line note",
            "",
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On"
        ]
        for i in range(75):
            csv_rows.append(f"Person{i},Test{i},https://linkedin.com/in/person{i},,Company{i},Role{i},01 Jan 2026")
        
        parsed = parse_connections_csv("\n".join(csv_rows))
        self.assertEqual(len(parsed), 75)
        self.assertEqual(parsed[0].name, "Person0 Test0")
        self.assertEqual(parsed[74].name, "Person74 Test74")

    def test_photo_url_extraction(self):
        """Verify that legitimate photo URLs are mapped if present in CSV."""
        csv_content = """Notes:
note

First Name,Last Name,URL,Email Address,Company,Position,Connected On,Picture
Alice,Wonder,https://linkedin.com/in/alice,,Acme,Dev,01 Jan 2026,https://media.licdn.com/dms/image/alice.jpg
Bob,Builder,https://linkedin.com/in/bob,,BuildCo,Builder,02 Jan 2026,
"""
        parsed = parse_connections_csv(csv_content)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].picture, "https://media.licdn.com/dms/image/alice.jpg")
        self.assertIsNone(parsed[1].picture)


if __name__ == "__main__":
    unittest.main()
