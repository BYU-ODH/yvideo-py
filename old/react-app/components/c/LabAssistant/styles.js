import styled from 'styled-components'

import searchIcon from 'assets/search.svg'

const Style = styled.div`
	display: flex;
	flex-direction: column;
	align-items: center;
	height: calc(100vh - 8.4rem);
	background-color: #fafafa;
	padding-top: 8.4rem;

	& > div {
		display: flex;
	}

	& > span {
		margin: 5rem 0 4rem 0;
		font-weight: normal;
	}

	@media screen and (max-width: 1000px) {
		text-align: center;
	}
`

export default Style

export const Search = styled.form`
	position: relative;
	text-align: ${props => props.isMobile && `center`};

	& > input {
		z-index: 1;
		background: white;

		height: 4rem;
		width: 30rem;
		// width: 90%;

		font-size: 1.5rem;

		border: none;
		border-radius: 2rem;

		margin-left: 1rem;

		padding: 0 1.25rem 0 3.25rem;

		outline: none;
		box-shadow: 0px 2px 5px -1px rgba(0, 0, 0, 0.15);
	}

	& > button {
		width: 8rem;
    height: 4rem;
    color: white;
    background-color: var(--light-blue);
    margin-left: 1rem;
    outline: none;
    box-shadow: 0px 2px 5px -1px rgba(0, 0, 0, 0.15);
    font-size: 1.5rem;
    border: none;
    border-radius: 2rem;
    text-align: center;
		cursor: pointer;
		font-weight: 500;

		:hover {
			background-color: var(--navy-blue);
		}

	}
`

export const SearchIcon = styled.span`
	position: absolute;
	z-index: 10;
	top: 1rem;
	left: 2rem;
	background: url(${searchIcon}) center no-repeat;
	background-size: contain;
	height: 2rem;
	width: 2rem;

	// @media screen and (max-width: 1000px) {
	// 	left: 5.5rem;
	// }
`
export const FeedbackMessage = styled.div`
	height: 100px;
	display: flex;
  justify-content: center;
  align-items: center;

	& > p {
		font-weight: 200;
		font-size: 20px;
		margin: auto;
	}
`
