# AWS-Rekognition
Serverless Image Moderation &amp; Auto-Tagging Pipeline

A fully serverless AWS pipeline that automatically tags uploaded images and screens them for unsafe content using **AWS Rekognition** — built entirely through the AWS Management Console.

---

## What This Project Does

When an image is uploaded to an S3 bucket, the system automatically:

1. **Tags the image** — detects objects, scenes, and people in the photo (e.g., "Person", "Kitchen", "Fire")
2. **Screens the image for unsafe content** — checks for categories like alcohol, violence, or explicit material
3. **Stores the results** — saves all labels and moderation data to a database for later lookup
4. **Quarantines flagged content** — if something unsafe is detected, the image is automatically moved out of the public-facing folder
5. **Sends an alert** — an email notification is sent the moment something gets flagged

No servers, no manual review needed to kick things off — the whole thing runs automatically the second a file lands in the bucket.

---

## Real-World Use Case

This mirrors what companies actually build for:
- **Social media / dating apps** — screening user-uploaded profile photos
- **Marketplaces** — auto-tagging product listing images and blocking inappropriate uploads
- **Content platforms** — moderating user-generated content at scale without a human reviewing every image

---

## Architecture

```
User/App
   |
   v
[S3 Bucket] --(upload to incoming/)--> triggers
   |
   v
[Lambda: RekognitionImageProcessor]
   |
   |--> [Rekognition] detect_labels()            --> auto-tags (e.g. "Person", "Kitchen")
   |--> [Rekognition] detect_moderation_labels()  --> safety check (e.g. "Alcohol", "Violence")
   |
   |--> [DynamoDB: ImageResults] --> stores tags + moderation results + flagged status
   |
   +--(if flagged)--> [S3] moves file: incoming/ -> quarantine/
   |
   +--(if flagged)--> [SNS Topic] --> sends email alert
```

**Optional extension (not required for core function):**
```
[API Gateway] --> [Lambda: GetImageResult] --> reads from DynamoDB --> returns JSON
```
This lets any external app query the tags/status of a processed image by ID over a simple HTTP endpoint.

---

## AWS Services Used

| Service | Role |
|---|---|
| **S3** | Stores uploaded images (`incoming/`) and quarantined images (`quarantine/`) |
| **Lambda** | Runs the processing logic automatically on every upload |
| **Rekognition** | AI service that detects objects/scenes and screens for unsafe content |
| **DynamoDB** | NoSQL database storing every image's tags and moderation results |
| **SNS** | Sends email alerts when something gets flagged |
| **IAM** | Grants the Lambda function permission to talk to the other services |
| **API Gateway** *(optional)* | Exposes results over a public HTTP endpoint |
| **CloudWatch** | Logs every Lambda run — this is what we used throughout to debug issues |

---

## Region Note

Everything was deployed in a single AWS region (**ap-south-1 / Asia Pacific, Mumbai**) except IAM, which is global. S3 event triggers only work when the bucket and the Lambda function are in the same region, so this consistency was required, not optional.

---

## The Build Process

1. Created an **S3 bucket** with two folders: `incoming/` (uploads land here) and `quarantine/` (flagged images get moved here)
2. Created a **DynamoDB table** (`ImageResults`) to store the results of every processed image
3. Created an **SNS topic** and subscribed an email address to receive alerts
4. Created an **IAM role** giving Lambda permission to use S3, Rekognition, DynamoDB, and SNS
5. Created a **Lambda function** that runs Rekognition's `detect_labels` and `detect_moderation_labels` on every new image, then writes results to DynamoDB
6. Connected **S3 → Lambda** using an event trigger, so uploads automatically kick off processing
7. Added quarantine + email alert logic for anything flagged as unsafe
8. Tested the full pipeline end-to-end with real images

---

## Issues Hit During Development & How They Were Fixed

This project wasn't a smooth "deploy once and it works" experience — real debugging happened at almost every stage. That's actually a good thing to have on a CV, since it shows you can diagnose and fix real AWS issues, not just copy a tutorial.

### 1. S3 bucket creation failed: "conflicting conditional operation"
**Cause:** Clicking "Create bucket" more than once, or reusing a bucket name too soon after a delete (S3 bucket names are globally unique across *all* AWS accounts and need time to release).
**Fix:** Waited briefly and used a slightly different, unique bucket name.

### 2. `TypeError: Float types are not supported. Use Decimal types instead.`
**Cause:** DynamoDB's Python SDK (`boto3`) does not accept native Python `float` values — only `Decimal`. The Lambda code was storing Rekognition's confidence scores (which are floats) directly.
**Fix:** Converted every confidence score to `Decimal` before writing to DynamoDB:
```python
from decimal import Decimal
"confidence": Decimal(str(round(l['Confidence'], 2)))
```

### 3. `InvalidImageFormatException`
**Cause:** Rekognition only accepts JPEG or PNG images. A test upload wasn't in a supported format.
**Fix:** Re-tested with a confirmed real `.jpg` file, which processed successfully.

### 4. Quarantine folder stayed empty even after uploading "risky-looking" images
**Cause:** This wasn't actually a bug — Rekognition correctly determined that photos like a bonfire scene or a firefighter training photo were safe, so nothing was flagged. The `MinConfidence=60` parameter on `detect_moderation_labels()` also meant Rekognition was filtering out low-confidence results *before* they ever reached the code's own threshold logic.
**Fix:** Temporarily lowered both `MinConfidence` (in the Rekognition API call) and `MODERATION_THRESHOLD` (in the code's own flagging logic) to force a test flag, confirmed the quarantine + alert logic worked, then reset both to realistic production values afterward.

### 5. SNS email alert never arrived, even though DynamoDB showed `flagged: true`
**Cause:** A second, separate Decimal bug — this time in the SNS message itself. The alert text was built using Python's built-in `json.dumps()`, which (unlike `boto3`) does **not** know how to serialize `Decimal` objects. This caused the function to crash *after* writing to DynamoDB and moving the file to quarantine, but *before* it ever reached the `sns.publish()` call — explaining why everything looked successful except the email.
**Fix:** Added a small converter function so `json.dumps()` could handle `Decimal` values when building the alert message:
```python
def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

json.dumps(moderation_labels, indent=2, default=decimal_default)
```

**Key lesson from this whole debugging arc:** `Decimal` conversion is required in *two completely separate places* when mixing DynamoDB and JSON serialization in the same Lambda — once for the database write, and again for anything (like an SNS message) that later tries to convert that same data back to JSON.

---

## Verified Working End-to-End

Final testing confirmed the full pipeline works correctly:
- ✅ Safe images (kitchen scene, group photo, bonfire, firefighter training) → tagged correctly, `flagged: false`, stayed in `incoming/`
- ✅ A wine/alcohol test image → correctly flagged (`Alcohol` and `Alcoholic Beverages`, ~97% confidence), moved to `quarantine/`, and triggered a real SNS email alert

---

## What's Left / Possible Improvements

- Replace broad `FullAccess` IAM policies with least-privilege, scoped permissions
- Add the optional **API Gateway** endpoint so external apps can query image results over HTTP
- Add a simple frontend to upload images and view results without going through the AWS Console
- Rewrite the infrastructure as code (CloudFormation / Terraform / AWS SAM) instead of manual console setup, for repeatable deployments
- Add automated tests and a CI/CD pipeline for the Lambda code
- Add `CompareFaces` for a KYC-style "does this selfie match this ID" feature

---


## Tech Stack Summary 

**AWS Rekognition · AWS Lambda · Amazon S3 · Amazon DynamoDB · Amazon SNS · IAM · CloudWatch**

> Built a serverless image moderation and auto-tagging pipeline using AWS Rekognition, Lambda, S3, and DynamoDB. Implemented automated content-safety flagging with quarantine handling and SNS email alerting. Debugged and resolved multiple real-world issues including DynamoDB Decimal-type serialization errors and Rekognition confidence-threshold filtering, verified through CloudWatch log analysis.

## Reference (Demo)

<img width="1917" height="973" alt="sns" src="https://github.com/user-attachments/assets/a8ade915-9559-4332-a978-5ccd570ed5b8" />
<img width="1920" height="973" alt="s3" src="https://github.com/user-attachments/assets/dbed4841-e19d-4332-9197-91715fe4c91b" />
<img width="1912" height="973" alt="quarantine" src="https://github.com/user-attachments/assets/0f429ba0-38f3-435a-b597-b73a0f7023b6" />
<img width="1912" height="967" alt="lamda" src="https://github.com/user-attachments/assets/c7105712-5742-4b33-8a9b-fa6110720fc4" />
<img width="1917" height="963" alt="incoming" src="https://github.com/user-attachments/assets/4bdb1ffa-3276-4887-b551-0b7377b0e6b2" />
<img width="1918" height="966" alt="dynamo" src="https://github.com/user-attachments/assets/165a3e1d-a6b0-44c3-b261-d9e591e1d68d" />
<img width="1918" height="971" alt="cloudwatch" src="https://github.com/user-attachments/assets/e872e05c-6eb9-49b6-8093-920bd2447a98" />
