from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
from bunny import BunnyUploader
import pdf


class IssueManager:
    ISSUE_RANGES = {1: "Jan-March", 2: "April-June", 3: "July-Sept", 4: "Oct-Dec"}

    def __init__(self, current_issue_path, archive_path):
        self.current_issue_path = current_issue_path
        self.archive_path = archive_path
        self.current_year = datetime.now().year

    def extract_volume_issue(self, text):
        """Extract volume and issue numbers from text"""
        match = re.search(r"Vol\s+(\d+)\s*-\s*No\.\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None, None

    def get_current_issue_info(self):
        """Get current issue's volume and issue numbers"""
        with open(self.current_issue_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")
            blockquote = soup.find("blockquote", {"class": "text-blue-500"})
            if blockquote:
                return self.extract_volume_issue(blockquote.text)
        return None, None

    def update_link_format(self, content, vol, issue, year):
        """Update links to match the correct format"""
        soup = BeautifulSoup(content, "html.parser")

        # Update all links to match the format:
        # http://gjms.b-cdn.net/YEAR/Volume VOL/Issue ISSUE, YEAR/filename.pdf
        for link in soup.find_all("a"):
            href = link.get("href", "")
            if "gjms.b-cdn.net" in href:
                # Extract filename from existing link
                filename = href.split("/")[-1]
                # Create new properly formatted link
                new_href = f"http://gjms.b-cdn.net/{year}/Volume {vol}/Issue {issue}, {year}/{filename}"
                link["href"] = new_href

        return str(soup)

    def create_issue_block(self, content, vol, issue, year):
        """Create a formatted issue block with proper date range"""
        date_range = self.ISSUE_RANGES.get(issue, "")
        return f"""
        <div class="container mx-auto flex px-5 py-24 md:flex-row flex-col">
            <div class="lg:flex-grow md:w-1/2 lg:pr-24 md:pr-16 flex flex-col mb-16 md:mb-0 items-center text-center">
                <h1 class="title-font sm:text-4xl text-9xl mb-4 font-bold text-blue-500">Current Issue</h1>
                <div class="container px-5 py-5 mx-auto5 py-24 mx-auto">
                    <blockquote class="text-blue-500">Vol {vol} - No. {issue} ({date_range}, {year})</blockquote>
                    <details class="question py-4 border-b border-grey-lighter">
                        <table class="border-separate border-spacing-6 border border-slate-500 table-fixed">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Editor's Note</th>
                                    <th>Page #</th>
                                    <th>Read</th>
                                </tr>
                            </thead>
                            <tbody>
                                {self.update_link_format(content, vol, issue, year)}
                            </tbody>
                        </table>
                        <strong>Vol {vol} - No. {issue} ({date_range}, {year})</strong>
                    </details>
                    <hr/>
                </div>
            </div>
        </div>
        """

    def create_archive_block(self, content, vol, issue, year):
        """Create a formatted archive block"""
        date_range = self.ISSUE_RANGES.get(issue, "")
        return f"""
        <div class="container px-5 py-5 mx-auto5 py-24 mx-auto">
            <blockquote class="text-blue-500">Vol {vol} - No. {issue} ({date_range}, {year})</blockquote>
            <details class="question py-4 border-b border-grey-lighter">
                <table class="border-separate border-spacing-6 border border-slate-500 table-fixed">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Editor's Note</th>
                            <th>Page #</th>
                            <th>Read</th>
                        </tr>
                    </thead>
                    <tbody>
                        {self.update_link_format(content, vol, issue, year)}
                    </tbody>
                </table>
                <strong>Vol {vol} - No. {issue} ({date_range}, {year})</strong>
            </details>
            <hr/>
        </div>
        """

    def update_files(self, new_content, new_vol, new_issue):
        """Update both current-issue.html and archive.html files"""
        # Calculate year based on volume (assuming Vol 1 started in 2015)
        new_year = 2015 + (new_vol - 1)

        # Read current content
        with open(self.current_issue_path, "r", encoding="utf-8") as f:
            current_soup = BeautifulSoup(f, "html.parser")

        with open(self.archive_path, "r", encoding="utf-8") as f:
            archive_soup = BeautifulSoup(f, "html.parser")

        # Extract current content to move to archive
        current_content = current_soup.find(
            "div", {"class": "container px-5 py-5 mx-auto5 py-24 mx-auto"}
        )

        if current_content:
            # Move current content to archive
            archive_containers = archive_soup.findAll(
                "div", {"class": "container px-5 py-5 mx-auto5 py-24 mx-auto"}
            )
            if archive_containers:
                # Insert after the first container (which is usually the title section)
                archive_containers[-1].insert_after(
                    BeautifulSoup(str(current_content), "html.parser")
                )

        # Update current-issue with new content
        content_section = current_soup.find(
            "section", {"class": "text-white body-font mx-12"}
        )
        if content_section:
            new_content_soup = BeautifulSoup(
                self.create_issue_block(new_content, new_vol, new_issue, new_year),
                "html.parser",
            )
            content_section.clear()
            content_section.append(new_content_soup)

        # Save updated files
        with open(self.current_issue_path, "w", encoding="utf-8") as f:
            f.write(str(current_soup.prettify()))

        with open(self.archive_path, "w", encoding="utf-8") as f:
            f.write(str(archive_soup.prettify()))

        # After updating HTML files, upload PDFs to bunny

    def should_update(self, new_vol, new_issue):
        """Check if new content should replace current content"""
        current_vol, current_issue = self.get_current_issue_info()
        if current_vol is None or current_issue is None:
            return True

        if new_vol > current_vol:
            return True
        elif new_vol == current_vol and new_issue > current_issue:
            return True
        return False


def main():
    # Example usage
    upload_files = True
    new_content, volume, issue = pdf.main()
    new_content = "\n".join(new_content)
    new_vol = int(volume)
    new_issue = int(issue)

    if upload_files:
        try:
            uploader = BunnyUploader()
            success, errors = uploader.upload_directory(
                "test_files", new_vol, new_issue
            )
            print("\nBunny.net upload complete!")
            print(f"Successfully uploaded: {success} files")
            print(f"Failed uploads: {errors} files")
        except Exception as e:
            print(f"Error during bunny.net upload: {str(e)}")

    dir = "/Users/shop/gjms-frontend"
    os.chdir(dir)
    manager = IssueManager("current-issue.html", "archive.html")

    if manager.should_update(new_vol, new_issue):
        manager.update_files(new_content, new_vol, new_issue)
        print(f"Updated files with Vol {new_vol} Issue {new_issue}")
    else:
        print("No update needed")


if __name__ == "__main__":
    main()
