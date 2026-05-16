# Grayscale Technical Assessment

This project is intentionally small and open-ended. We don't expect a polished or complete solution. Cap your time at about two hours. We care more about how you think, the questions you ask, problems you identify and overall technical acumen rather than the shippable code. We will review code if provided, but we won't negatively judge submissions that are architecture-only.

You're welcome to use AI tools to help you work, but note that ultimately we're looking for subject matter experts (SMEs) who can express thought leadership.

## Background

A small fund's operations team reconciles end-of-day positions from multiple custodians every morning. The work is mostly manual. They want to automate it.

This folder contains:

* `securities_reference.csv`, the canonical security master.
* `custodian_a.csv` and `custodian_b.csv`, end-of-day positions for Jan 2, 2026, from two custodians, in two different shapes.

## Additional Context on Reconciliation

Reconciliation is the daily comparison between what the fund thinks it holds and what each custodian says it holds. The fund keeps its own book of record (an OMS or PMS that captures every trade as it executes). The custodians hold the actual assets and keep their own records of what's settled. Every morning ops gets an end-of-day file from each custodian, and the job before markets reopen is to confirm those line up with the book.

Example differences might include differences in quantities, position holdings, market valuations. Reconciliation issues arise for a variety of reasons: trade settlement timing, corporate action adjustments, FX, custodian errors, etc. Packaging and formatting issues from the custodian sometimes contribute as well.

When a mismatch occurs, a labor-intensive data-tracing process begins for the fund. It involves things like pulling recent trade tickets, checking the corporate action calendar, etc. Everything has to be cleared, or at least understood, before markets reopen. Ideally, the organization wants to completely eliminate this problem.

## Problem Scope and Deliverables

There are two sub-problems in this exercise.

First: data processing. How would you represent and process the data so that downstream analysis is straightforward and effective?

Second: your acumen and ability to architect a solution. How would you completely eliminate this problem for the business? What tools, technologies, etc. would you use? Do you see AI being relevant to this problem and if so, how?

### Evaluation

We'll happily review any written code, but also will not negatively evaluate submissions that only express a solution architecture. Solutions with an diagram will be viewed favorably. If you decide to write code, any programming language is fine but please consider instructions for installing dependencies and running it.

In writing up the problem, we will not negatively evaluate evolved thinking. For example it's perfectly fine to "pivot" in your writeup or analysis. If you start by thinking X would be a good solution but then develop concerns and would want to explore Y, then document that and talk the potential issues and concerns. Information about what you considered, what you rejected and why, is helpful communication.

## Deliverables

Please keep the content simple and easy to discover:
- README.md: a single file writeup/summary. All content around specs, description/summary, any problematic or areas of concern, details any provided code, as well as what questions you would ask and what you would do next can all go here
- A `code/` or `src/` folder, if you write any code
- A `img/` folder to contain any diagrams you create

---

Good luck.
