from pygerber.gerber.api._gerber_job_file import GerberJobFile

gerber_job = GerberJobFile.from_file("test-job.gbrjob")
project = gerber_job.to_project()

print("top:")
for file in project.top.files:
    print(f"  {file}")

for i, inner in enumerate(project.inner):
    print(f"inner {i}:")
    for file in inner.files:
        print(f"  {file}")

print("bottom:")
for file in project.bottom.files:
    print(f"  {file}")

project.top.render_with_pillow().get_image().save("output_top.png")
print("Rendered output_top.png")
print("Done - Gerber files parsed successfully!")
