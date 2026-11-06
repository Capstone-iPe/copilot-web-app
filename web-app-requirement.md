# Web app requirements

A merchant applies for one shop in one conversation. This document says what the website must do. It does not say how the website talks to the chat service.

The pages follow the prototype at [capstone-ipe.github.io/analysis](https://capstone-ipe.github.io/analysis/). The chat model is not part of this work. It is not chosen yet.

## Sign in

The merchant signs in with Google. The website remembers that sign-in and refreshes it when it expires. The Google secret stays off the website. Reach out to Rupesh Sasne for the Google keys.

This sign-in is for the merchant. It is separate from the sign-in Claude and ChatGPT use to reach the onboarding service.

## One shop, one conversation

The merchant applies for a single shop in a single conversation. The questions, the documents, the summary, and the result all stay there.

A second shop is a new conversation. It does not continue inside the first one.

The side of the page shows the shop name, the status, and the review number once one exists. Before submit the status is Draft. After submit it is In review, Recommend go-live, or Refused. The side also shows three steps: shop details, documents, and submitted.

## The conversation

The website asks for one thing at a time.

The merchant can type an answer. Asking why something is needed gets an explanation and then the same question again. The application does not change.

A document is a PDF, JPG, or PNG. The merchant can also say they do not have it. Before submit, the page offers the one document that is still being asked for.

When the questions are done, the website reads the application back: shop name, city, business type, monthly volume, payment methods, whether payer phone numbers are stored, each document, and the note. Answers already given stay as given. A request to change the city, the volume, or any other saved answer does not rewrite it.

Submit is available only when every required document is uploaded or the merchant has said they do not have it. The note is optional.

The documents are the business registration, the owner's government ID, bank proof in the shop's legal name, and a shopfront photo. A signed data-processing addendum is asked for only when the shop stores payer phone numbers.

## The result

The result stays in the same conversation. It says one of three things: the shop is in review, go-live may be recommended, or the application is refused. It names what is still missing, who at iPe reviews the shop, and the policy behind the result.

A review number is shown when the shop is in review or go-live may be recommended. That number does not change. A refused application has no review number.

The conversation stays open. Each missing document has an upload button. Sending the file updates the result. The review number stays the same.

There is no button that turns the shop on. If the merchant says to activate the shop or to mark it active, the reply says a person at iPe does that. The review number stays the same. Recommend go-live means a person may turn the shop on. The shop is not live yet.

## Coming back

A refresh, or signing in again, shows the same conversation for that shop.

## What the website does not do

The website does not decide the result. It does not turn a shop on. It does not change a saved answer. It does not talk to the onboarding service. The chat service does that.
