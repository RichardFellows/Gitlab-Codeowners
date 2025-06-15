import os
import sys
import json
from datetime import datetime, timedelta, timezone
from collections import Counter

# --- Web Framework and GGL Imports ---
from flask import Flask, render_template, request, Response, stream_with_context
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

# --- Flask App Initialization ---
app = Flask(__name__)

# --- 1. CONFIGURATION ---
NEW_BRANCH_NAME = "feature/add-codeowners-file"
COMMIT_MESSAGE = "feat: Add CODEOWNERS file"
MR_TITLE = "Chore: Add CODEOWNERS file"
MR_DESCRIPTION = """
This MR adds a `CODEOWNERS` file to the repository to define code ownership and
facilitate the review process.

The suggested owners have been automatically determined based on recent merge
request approvals over the last 90 days.
"""
DEFAULT_CODEOWNERS_HEADER = """
# Lines starting with '#' are comments.
# Each line is a pattern followed by one or more owners.

* @everyone # Default owner for all files
"""
DAYS_TO_LOOK_BACK = 90
MAX_SUGGESTED_OWNERS = 5


# --- 2. GRAPHQL QUERIES (Complete and Corrected) ---
GET_GROUP_DETAILS_QUERY = gql("""
    query getGroupDetails($groupFullPath: ID!) {
      group(fullPath: $groupFullPath) {
        id
        name
        fullPath
        projects(first: 1, includeSubgroups: true) {
          count
        }
      }
    }
""")

GET_PROJECTS_QUERY = gql("""
    query getGroupProjects($groupFullPath: ID!, $after: String) {
      group(fullPath: $groupFullPath) {
        projects(first: 50, after: $after, includeSubgroups: true) {
          nodes {
            id
            name
            fullPath
            repository {
              rootRef
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
""")

CHECK_CODEOWNERS_QUERY = gql("""
    query checkFileExistence($projectPath: ID!) {
      project(fullPath: $projectPath) {
        repository {
          blobs(paths: ["CODEOWNERS", ".gitlab/CODEOWNERS", "docs/CODEOWNERS"]) {
            nodes {
              path
            }
          }
        }
      }
    }
""")

GET_APPROVERS_QUERY = gql("""
    query getRecentApprovers($projectPath: ID!, $mergedAfter: Time!) {
      project(fullPath: $projectPath) {
        mergeRequests(state: merged, mergedAfter: $mergedAfter, first: 50, sort: MERGED_AT_DESC) {
          nodes {
            approvedBy {
              nodes {
                username
              }
            }
          }
        }
      }
    }
""")

CREATE_COMMIT_MUTATION = gql("""
    mutation createCommit(
        $projectPath: ID!,
        $branch: String!,
        $startBranch: String!,
        $message: String!,
        $actions: [CommitAction!]!
    ) {
      commitCreate(input: {
        projectPath: $projectPath,
        branch: $branch,
        startBranch: $startBranch,
        message: $message,
        actions: $actions
      }) {
        commit { id }
        errors
      }
    }
""")

CREATE_MR_MUTATION = gql("""
    mutation createMergeRequest(
        $projectPath: ID!,
        $sourceBranch: String!,
        $targetBranch: String!,
        $title: String!,
        $description: String!
    ) {
      mergeRequestCreate(input: {
        projectPath: $projectPath,
        sourceBranch: $sourceBranch,
        targetBranch: $targetBranch,
        title: $title,
        description: $description
      }) {
        mergeRequest {
          webUrl
        }
        errors
      }
    }
""")


# --- 3. GitLabScanner Class ---
class GitLabScanner:
    def __init__(self, gitlab_url, token):
        self.gitlab_url = gitlab_url
        if not token:
            raise ValueError("API Token is required.")
        self.client = self._initialize_gql_client(token)

    def _initialize_gql_client(self, token):
        transport = RequestsHTTPTransport(
            url=f"{self.gitlab_url}/api/graphql",
            headers={"Authorization": f"Bearer {token}"},
            verify=True, retries=3)
        return Client(transport=transport, fetch_schema_from_transport=False)

    def execute_query(self, query, params=None):
        try:
            return self.client.execute(query, variable_values=params)
        except Exception as e:
            raise e

    def get_all_projects(self, group_path):
        yield f"<div class='log-info'>Fetching projects from group '{group_path}'...</div>"
        projects = []
        has_next_page = True
        after_cursor = None
        while has_next_page:
            params = {"groupFullPath": group_path, "after": after_cursor}
            response = self.execute_query(GET_PROJECTS_QUERY, params)
            if not response or not response.get("group"):
                raise Exception("Could not fetch projects. Check group name and token permissions.")
            project_nodes = response["group"]["projects"]["nodes"]
            projects.extend(project_nodes)
            page_info = response["group"]["projects"]["pageInfo"]
            has_next_page = page_info["hasNextPage"]
            after_cursor = page_info["endCursor"]
            yield f"<div class='log-info'>Found {len(projects)} projects so far...</div>"
        yield f"<div class='log-success'>Completed project discovery. Found {len(projects)} total repositories.</div>"
        return projects

    def check_codeowners_exist(self, project_path):
        params = {"projectPath": project_path}
        response = self.execute_query(CHECK_CODEOWNERS_QUERY, params)
        if response and response.get("project") and response["project"].get("repository"):
            return bool(response["project"]["repository"]["blobs"]["nodes"])
        return False

    def get_suggested_owners(self, project_path):
        merged_after = datetime.now(timezone.utc) - timedelta(days=DAYS_TO_LOOK_BACK)
        params = {"projectPath": project_path, "mergedAfter": merged_after.isoformat()}
        response = self.execute_query(GET_APPROVERS_QUERY, params)
        if not response or not response.get("project") or not response["project"].get("mergeRequests"):
            return []
        all_approvers = [
            approver["username"]
            for mr in response["project"]["mergeRequests"]["nodes"]
            for approver in mr["approvedBy"]["nodes"]
        ]
        if not all_approvers:
            return []
        approver_counts = Counter(all_approvers)
        most_common = [owner for owner, count in approver_counts.most_common(MAX_SUGGESTED_OWNERS)]
        return most_common

    def run_scan(self, group_path):
        projects_to_fix = []
        try:
            yield f"<div><strong>Starting Scan for Group:</strong> {group_path}</div><hr>"
            projects = yield from self.get_all_projects(group_path)
            for i, project in enumerate(projects, 1):
                yield f"<div class='log-info'>[{i}/{len(projects)}] Scanning: {project['name']}</div>"
                if self.check_codeowners_exist(project['fullPath']):
                    yield f"<div class='log-info'>&nbsp;&nbsp;-> Found existing CODEOWNERS file.</div>"
                else:
                    yield f"<div class='log-action'>&nbsp;&nbsp;-> No CODEOWNERS file found. Action required.</div>"
                    default_branch = project.get('repository', {}).get('rootRef')
                    projects_to_fix.append({
                        'fullPath': project['fullPath'],
                        'name': project['name'],
                        'defaultBranch': default_branch
                    })
            yield f"<hr><div class='log-success'><strong>Scan Complete!</strong> Found {len(projects_to_fix)} project(s) needing a CODEOWNERS file.</div>"
        except Exception as e:
            yield f"<div class='log-error'><strong>ERROR during scan:</strong> {e}</div>"
        return projects_to_fix

    def run_fix(self, projects_to_fix, branch_name, codeowners_template, mr_description):
        yield "<div><strong>Starting MR Creation Process...</strong></div><hr>"
        created_count = 0
        total_mrs_to_create = len(projects_to_fix)
        
        for i, project in enumerate(projects_to_fix, 1):
            try:
                yield f"<div class='log-info'>[{i}/{total_mrs_to_create}] Fixing project: {project['name']}</div>"
                if not project["defaultBranch"]:
                    yield f"<div class='log-error'>&nbsp;&nbsp;-> SKIPPING: Project has an empty repository or no default branch.</div>"
                    continue

                # Get suggested owners and replace placeholder in the template
                suggested_owners = self.get_suggested_owners(project['fullPath'])
                owner_string = " ".join([f"@{owner}" for owner in sorted(suggested_owners)])
                final_content = codeowners_template.replace('{suggested_owners}', owner_string)
                
                yield f"<div class='log-info'>&nbsp;&nbsp;-> Suggested owners found: {suggested_owners or ['(none)'] }</div>"
                yield f"<div class='log-info'>&nbsp;&nbsp;-> Using branch name: '{branch_name}'</div>"
                
                commit_actions = [{"action": "CREATE", "filePath": "CODEOWNERS", "content": final_content}]
                commit_params = {
                    "projectPath": project["fullPath"], "branch": branch_name, # Use variable
                    "startBranch": project["defaultBranch"], "message": COMMIT_MESSAGE,
                    "actions": commit_actions,
                }
                commit_response = self.execute_query(CREATE_COMMIT_MUTATION, commit_params)
                if commit_response.get("commitCreate", {}).get("errors"):
                    raise Exception(f"Failed to create commit: {commit_response['commitCreate']['errors']}")

                mr_params = {
                    "projectPath": project["fullPath"], "sourceBranch": branch_name, # Use variable
                    "targetBranch": project["defaultBranch"], "title": MR_TITLE,
                    "description": mr_description,
                }
                mr_response = self.execute_query(CREATE_MR_MUTATION, mr_params)
                if mr_response.get("mergeRequestCreate", {}).get("errors"):
                    raise Exception(f"Failed to create MR: {mr_response['mergeRequestCreate']['errors']}")
                
                web_url = mr_response["mergeRequestCreate"]["mergeRequest"]["webUrl"]
                yield f"<div class='log-success'>&nbsp;&nbsp;-> SUCCESS: MR created at <a href='{web_url}' target='_blank'>{web_url}</a></div>"
                created_count += 1
            except Exception as e:
                yield f"<div class='log-error'>&nbsp;&nbsp;-> FAILED for project {project['name']}: {e}</div>"

        yield f"<hr><div class='log-success'><strong>Fix Process Complete! Created {created_count} / {total_mrs_to_create} Merge Requests.</strong></div>"


# --- 4. Flask Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scan", methods=["POST"])
def scan():
    # Retrieve all form data
    token = request.form.get("token")
    group = request.form.get("group")
    gitlab_url = request.form.get("gitlab_url") or "https://gitlab.com"
    branch_name = request.form.get("branch_name")
    codeowners_content = request.form.get("codeowners_content")
    
    scanner = GitLabScanner(gitlab_url, token)
    
    def generate_scan_output():
        projects_to_fix = yield from scanner.run_scan(group)
        # Pass the new config values to the results template
        yield render_template("results_form.html", projects=projects_to_fix, 
                              token=token, gitlab_url=gitlab_url, group=group,
                              branch_name=branch_name, codeowners_content=codeowners_content)
        
    return Response(stream_with_context(generate_scan_output()), mimetype="text/html")

@app.route("/execute", methods=["POST"])
def execute():
    # Retrieve all config from the hidden form fields
    token = request.form.get("token")
    gitlab_url = request.form.get("gitlab_url")
    branch_name = request.form.get("branch_name")
    codeowners_content = request.form.get("codeowners_content")
    projects_json = request.form.get("projects_to_fix")
    projects_to_fix = json.loads(projects_json)

    # Use a generic MR description for now
    mr_description = "This MR adds a CODEOWNERS file to define code ownership."

    scanner = GitLabScanner(gitlab_url, token)
    
    # Call run_fix with the new parameters
    return Response(stream_with_context(scanner.run_fix(projects_to_fix, branch_name, codeowners_content, mr_description)), mimetype="text/html")

if __name__ == "__main__":
    # You will need to re-add the full, non-omitted GraphQL queries here
    # when running this file directly for local debugging without docker-compose.
    app.run(host="0.0.0.0", port=5001, debug=True)